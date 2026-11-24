"""多页来源 HTML 解析。

把一页 HTML 拆成若干报价条目，并保留条目在原始页面内的位置。解析结果刻意区分
两种情况：

* ``提取失败``：价格栏缺失、为空或无法解析成数字，报价为 ``None``，进入待补位置；
* ``零价``：页面明确给出 ``0`` / ``0.00`` / ``￥0``，报价为 ``0``，是有效报价。

这里的实现只依赖标准库，既能解析结构化的 ``items``，也能从没有固定模板的
HTML 片段里按 class/id 语义和价格文本兜底识别条目。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 轻量 DOM
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class Node:
    """HTML 节点。``parts`` 按原始顺序保存文本和子节点，避免丢失文本顺序。"""

    __slots__ = ("tag", "attrs", "parts", "parent", "order")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None,
                 parent: "Node | None" = None, order: int = 0) -> None:
        self.tag = tag.lower()
        self.attrs = attrs or {}
        self.parts: list[str | Node] = []
        self.parent = parent
        self.order = order

    @property
    def classes(self) -> list[str]:
        return [item for item in self.attrs.get("class", "").split() if item]

    @property
    def node_id(self) -> str:
        return self.attrs.get("id", "")

    @property
    def class_id(self) -> str:
        return f"{self.attrs.get('class', '')} {self.node_id}".casefold()

    def walk(self) -> Iterable["Node"]:
        yield self
        for part in self.parts:
            if isinstance(part, Node):
                yield from part.walk()

    def text(self) -> str:
        chunks: list[str] = []
        for part in self.parts:
            if isinstance(part, str):
                chunks.append(part)
            else:
                value = part.text()
                if value:
                    chunks.append(value)
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()

    def own_text(self) -> str:
        return re.sub(
            r"\s+", " ", " ".join(p for p in self.parts if isinstance(p, str))
        ).strip()

    def contains_descendant(self, other: "Node") -> bool:
        current = other.parent
        while current is not None:
            if current is self:
                return True
            current = current.parent
        return False


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None, 0)
        self.stack: list[Node] = [self.root]
        self.counter = 0

    def _new_node(self, tag: str, attrs: list[tuple[str, str | None]]) -> Node:
        self.counter += 1
        clean = {key.lower(): (value or "") for key, value in attrs}
        return Node(tag, clean, self.stack[-1], self.counter)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = self._new_node(tag, attrs)
        self.stack[-1].parts.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = self._new_node(tag, attrs)
        self.stack[-1].parts.append(node)

    def handle_endtag(self, tag: str) -> None:
        # 容错：遇到不匹配的闭合标签时向上找最近同名节点，找不到就忽略。
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].parts.append(data)


_NAME_HINTS = (
    "name", "title", "product", "goods", "commodity", "sku", "offer-name",
    "nm", "item-name",
    "商品", "名称", "品名", "标题",
)
_PRICE_HINTS = (
    "price", "amount", "cost", "money", "money-value", "value", "sale",
    "pr", "amt", "fee", "charge",
    "报价", "价格", "售价", "单价",
)
_ITEM_HINTS = (
    "item", "product", "quote", "offer", "goods", "entry", "card", "row",
    "list-item", "commodity", "record",
)
_POSITION_ATTRS = ("data-position", "data-index", "data-pos", "data-item", "data-row")
_NAME_ATTRS = ("data-name", "data-title", "data-product", "data-goods", "title")
_PRICE_ATTRS = ("data-price", "data-amount", "data-cost", "data-value", "price")

_PRICE_IN_TEXT = re.compile(r"[¥￥$€£]\s*-?\d[\d,]*(?:\.\d+)?")
_NUMBER_IN_TEXT = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_BAD_PRICE_WORDS = (
    "--", "—", "–", "－", "n/a", "na", "null", "none", "nan", "undefined",
    "暂无", "待补", "缺失", "损坏", "异常", "错误", "面议", "未知", "不详",
    "xxx", "???", "###", "不适用",
)


def _first_attr(node: Node, keys: Iterable[str]) -> str:
    for key in keys:
        value = node.attrs.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _has_class_hint(node: Node, hints: Iterable[str]) -> bool:
    tokens = [
        token for token in re.split(r"[\s_\-]+", node.class_id) if token
    ]
    words = tokens + [node.class_id]
    for hint in hints:
        hint = hint.casefold()
        if hint.isascii():
            # 英文缩写（如 pr、nm）只认完整词段，避免 pr 命中 product。
            if any(token == hint for token in words):
                return True
        elif hint in node.class_id:
            return True
    return False


def _descendants(node: Node) -> Iterable[Node]:
    for child in node.walk():
        if child is not node:
            yield child


def _looks_like_price_node(node: Node) -> bool:
    if _first_attr(node, _PRICE_ATTRS):
        return True
    if _has_class_hint(node, _PRICE_HINTS):
        return True
    text = node.text()
    return bool(_PRICE_IN_TEXT.search(text))


def _looks_like_name_node(node: Node) -> bool:
    if _first_attr(node, _NAME_ATTRS):
        return True
    return _has_class_hint(node, _NAME_HINTS) and not _has_class_hint(node, _PRICE_HINTS)


def _has_price_descendant(node: Node) -> bool:
    return any(_looks_like_price_node(child) for child in _descendants(node))


def _has_name_descendant(node: Node) -> bool:
    return any(_looks_like_name_node(child) for child in _descendants(node))


def _has_heading_descendant(node: Node) -> bool:
    return any(
        child.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "a", "strong", "b"}
        for child in _descendants(node)
    )


# ---------------------------------------------------------------------------
# 选择器（只覆盖平台里常用的一小部分 CSS 写法）
# ---------------------------------------------------------------------------

_SELECTOR_PART = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?"
    r"(?P<id>#[\w-]+)?"
    r"(?P<classes>(?:\.[\w-]+)*)"
    r"(?P<attrs>(?:\[[^\]]+\])*)$"
)


def _match_simple_selector(node: Node, selector: str) -> bool:
    match = _SELECTOR_PART.match(selector.strip())
    if not match:
        return False
    if match.group("tag") and node.tag != match.group("tag").lower():
        return False
    if match.group("id") and node.node_id != match.group("id")[1:]:
        return False
    for class_name in re.findall(r"\.([\w-]+)", match.group("classes") or ""):
        if class_name not in node.classes:
            return False
    for raw_attr in re.findall(r"\[([^\]]+)\]", match.group("attrs") or ""):
        key, _, expected = raw_attr.partition("=")
        key = key.strip()
        expected = expected.strip().strip("\"'")
        if key not in node.attrs:
            return False
        if expected and node.attrs.get(key) != expected:
            return False
    return True


def _matches_selector(node: Node, selector: str) -> bool:
    """匹配逗号分隔的选择器；空格表示后代关系，按祖先链回溯。"""
    for option in selector.split(","):
        parts = option.strip().split()
        if not parts or not _match_simple_selector(node, parts[-1]):
            continue
        ancestors = parts[:-1]
        current = node.parent
        while ancestors and current is not None:
            if _match_simple_selector(current, ancestors[-1]):
                ancestors.pop()
            current = current.parent
        if not ancestors:
            return True
    return False


def _select(node: Node, selector: str) -> list[Node]:
    return [item for item in _descendants(node) if _matches_selector(item, selector)]


# ---------------------------------------------------------------------------
# 价格与名称
# ---------------------------------------------------------------------------

def parse_price(raw: Any) -> tuple[int | float | None, str]:
    """把价格原文解析成数字；返回 ``(价格, 失败原因)``。

    ``0`` 是有效价格；缺失、占位符、纯文字都会得到 ``(None, 原因)``。
    """
    if raw is None:
        return None, "价格栏缺失"
    if isinstance(raw, bool):
        return None, "价格栏不是有效数字"
    if isinstance(raw, (int, float)):
        return raw, ""
    text = str(raw).strip()
    if not text:
        return None, "价格栏为空"
    lowered = text.casefold()
    if any(word in lowered for word in _BAD_PRICE_WORDS) and not re.search(r"\d", text):
        return None, "价格栏损坏或为占位符"
    if not re.search(r"\d", text):
        return None, "价格栏不含数字"
    cleaned = re.sub(r"[,\s，]", "", text)
    match = _NUMBER_IN_TEXT.search(cleaned)
    if not match:
        return None, "价格栏无法解析"
    try:
        value = float(match.group(0))
    except ValueError:
        return None, "价格栏无法解析"
    if value.is_integer():
        return int(value), ""
    return value, ""


def _extract_price(node: Node, selector: str = "") -> tuple[Any, Node | None]:
    attr_price = _first_attr(node, _PRICE_ATTRS)
    if attr_price:
        return attr_price, node

    if selector:
        selected = _select(node, selector)
        if selected:
            return selected[0].text(), selected[0]

    for child in _descendants(node):
        if _first_attr(child, _PRICE_ATTRS) or _has_class_hint(child, _PRICE_HINTS):
            text = child.text()
            if text:
                return text, child
            return "", child  # 明确存在价格栏但为空，视为损坏，不用全文兜底

    # 没有价格节点时，用货币符号优先、否则最后一个数字兜底。
    text = node.text()
    currency = _PRICE_IN_TEXT.findall(text)
    target = currency[-1] if currency else None
    numeric = _NUMBER_IN_TEXT.findall(text)
    if target is None and numeric:
        target = numeric[-1]
    if target is None:
        return None, None

    # 定位承载这个数字的最深叶子节点，名称提取时好把它排除掉。
    holder = _deepest_text_holder(node, target)
    return (holder.text() if holder is not None else target), holder


def _deepest_text_holder(node: Node, needle: str) -> Node | None:
    for child in _descendants(node):
        if any(
            isinstance(part, Node) and needle in part.text()
            for part in child.parts
        ):
            continue
        if needle in child.own_text() and not any(
            isinstance(part, Node) for part in child.parts
        ):
            return child
    return None


def _extract_name(node: Node, price_node: Node | None, selector: str = "") -> str:
    attr_name = _first_attr(node, _NAME_ATTRS)
    if attr_name:
        return attr_name

    if selector:
        selected = _select(node, selector)
        if selected:
            text = selected[0].text().strip()
            if text:
                return text

    for child in _descendants(node):
        if _has_price_hint(child):
            continue
        if _looks_like_name_node(child):
            text = child.text().strip()
            if text:
                return text

    for child in _descendants(node):
        if child.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "a"}:
            if _has_price_hint(child):
                continue
            text = child.text().strip()
            if text:
                return text

    text = node.text()
    if price_node is not None and price_node is not node:
        price_text = price_node.text().strip()
        if price_text:
            text = text.replace(price_text, " ")
    text = _PRICE_IN_TEXT.sub(" ", text)
    text = re.sub(r"^(?:商品|名称|品名|标题)\s*[:：]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" :：,，|/-")
    return text


def _has_price_hint(node: Node) -> bool:
    return _looks_like_price_node(node) or _has_class_hint(node, _PRICE_HINTS)


def _node_locator(node: Node) -> str:
    explicit = _first_attr(node, _POSITION_ATTRS)
    if explicit:
        return f"[{_POSITION_ATTRS[0]}={explicit}]"
    if node.node_id:
        return f"#{node.node_id}"
    if node.classes:
        return f"{node.tag}.{'.'.join(node.classes)}"
    return node.tag


# ---------------------------------------------------------------------------
# 条目识别
# ---------------------------------------------------------------------------

def _item_signature(node: Node) -> tuple[Any, ...]:
    data_attrs = tuple(sorted(key for key in node.attrs if key.startswith("data-")))
    return (node.tag, tuple(sorted(node.classes)), data_attrs[:3])


def _drop_ancestors(nodes: list[Node]) -> list[Node]:
    """同一批候选里如果父节点也命中，只保留最深层的条目节点。"""
    result: list[Node] = []
    for node in nodes:
        if any(node.contains_descendant(other) for other in nodes if other is not node):
            continue
        result.append(node)
    return result


def _depth(node: Node) -> int:
    level = 0
    current = node.parent
    while current is not None:
        level += 1
        current = current.parent
    return level


def _find_item_nodes(root: Node, item_selector: str = "") -> list[Node]:
    if item_selector:
        selected = _select(root, item_selector)
        if selected:
            return _drop_ancestors(selected)

    positioned = [
        node for node in _descendants(root)
        if _first_attr(node, _POSITION_ATTRS)
    ]
    if positioned:
        return _drop_ancestors(positioned)

    groups: dict[tuple[Any, ...], list[Node]] = {}
    for node in _descendants(root):
        if node.tag in {"html", "body", "head", "script", "style"}:
            continue
        class_hint = _has_class_hint(node, _ITEM_HINTS)
        structured_tag = node.tag in {"li", "tr", "article"}
        generic = bool(node.classes or node.node_id)
        # 明确的条目信号（条目 class、列表标签）即使缺价格栏也要保留为待补；
        # 只有泛化容器才必须以“带价格”为前提，避免把外层包装当成条目。
        if class_hint or structured_tag:
            if not (
                _has_name_descendant(node)
                or _has_heading_descendant(node)
                or _has_price_descendant(node)
            ):
                continue
        elif generic:
            if not (_has_price_descendant(node) and (
                _has_name_descendant(node) or _has_heading_descendant(node)
            )):
                continue
        else:
            continue
        groups.setdefault(_item_signature(node), []).append(node)

    best: list[Node] = []
    best_score = -1
    for group in groups.values():
        score = len(group) * 10 + sum(_depth(node) for node in group)
        if any(_first_attr(node, _POSITION_ATTRS) for node in group):
            score += 3
        if any(_has_class_hint(node, _ITEM_HINTS) for node in group):
            score += 2
        if any(node.tag in {"li", "tr", "article"} for node in group):
            score += 2
        if score > best_score:
            best_score = score
            best = group
    if best:
        selected = _drop_ancestors(best)
        if selected:
            return selected

    # 单条详情页：取包含价格信息、且范围最小的那层容器。
    priced = [node for node in _descendants(root) if _looks_like_price_node(node)]
    for price_node in priced:
        current = price_node.parent
        while current is not None and current is not root:
            if _has_name_descendant(current):
                return [current]
            current = current.parent
    if priced:
        parent = priced[0].parent
        if parent is not None and parent is not root:
            return [parent]
    return []


def _position_from_node(node: Node, fallback: int) -> tuple[int, str]:
    raw = _first_attr(node, _POSITION_ATTRS)
    if raw:
        digits = re.search(r"\d+", raw)
        if digits:
            return int(digits.group(0)), _node_locator(node)
    return fallback, _node_locator(node)


# ---------------------------------------------------------------------------
# 页面解析
# ---------------------------------------------------------------------------

NAME_KEYS = (
    "name", "title", "product", "product_name", "商品", "商品名称", "名称",
    "品名", "标题",
)
PRICE_KEYS = (
    "price", "amount", "cost", "报价", "价格", "售价", "单价", "raw_price",
    "price_text",
)
POSITION_KEYS = (
    "position", "index", "pos", "item_index", "页内位置", "位置", "序号",
)


def _item_value(item: dict[str, Any], keys: Iterable[str]) -> tuple[Any, bool]:
    for key in keys:
        if key in item:
            return item[key], True
    return None, False


def _record_from_item(item: Any, page: int, fallback_position: int) -> dict[str, Any]:
    position = fallback_position
    locator = f"items[{fallback_position}]"
    name = ""
    raw_price: Any = None
    price_present = True

    if isinstance(item, dict):
        raw_pos, has_pos = _item_value(item, POSITION_KEYS)
        if has_pos and str(raw_pos).strip():
            digits = re.search(r"\d+", str(raw_pos))
            if digits:
                position = int(digits.group(0))
        raw_name, _ = _item_value(item, NAME_KEYS)
        name = str(raw_name or "").strip()
        raw_price, price_present = _item_value(item, PRICE_KEYS)
        if not price_present and "item" in item:
            raw_price = item.get("item")
    elif isinstance(item, str):
        parts = re.split(r"\s*[|｜\t]\s*", item)
        name = parts[0].strip()
        if len(parts) > 1:
            raw_price = parts[1]
        else:
            raw_price = item

    price, reason = parse_price(raw_price) if price_present else (None, "价格栏缺失")
    status = "可用" if reason == "" else "待补"
    record = {
        "record_id": f"P{page}-{position}",
        "来源页": page,
        "页码": page,
        "source_page": page,
        "页内位置": position,
        "位置": position,
        "position": position,
        "商品": name,
        "商品名称": name,
        "名称": name,
        "name": name,
        "title": name,
        "报价": price,
        "价格": price,
        "price": price,
        "原始价格": raw_price,
        "raw_price": raw_price,
        "零价": bool(price == 0),
        "zero_price": bool(price == 0),
        "提取状态": "成功" if status == "可用" else "失败",
        "extraction_status": "ok" if status == "可用" else "failed",
        "状态": status,
        "status": status,
        "失败原因": reason,
        "error": reason,
        "source_locator": locator,
    }
    return record


def _record_from_html(node: Node, page: int, position: int,
                      selectors: dict[str, str]) -> dict[str, Any]:
    name = _extract_name(node, None, selectors.get("name", ""))
    raw_price, price_node = _extract_price(node, selectors.get("price", ""))
    if not name:
        name = _extract_name(node, price_node, selectors.get("name", ""))
    price, reason = parse_price(raw_price)
    status = "可用" if reason == "" else "待补"
    return {
        "record_id": f"P{page}-{position}",
        "来源页": page,
        "页码": page,
        "source_page": page,
        "页内位置": position,
        "位置": position,
        "position": position,
        "商品": name,
        "商品名称": name,
        "名称": name,
        "name": name,
        "title": name,
        "报价": price,
        "价格": price,
        "price": price,
        "原始价格": raw_price,
        "raw_price": raw_price,
        "零价": bool(price == 0),
        "zero_price": bool(price == 0),
        "提取状态": "成功" if status == "可用" else "失败",
        "extraction_status": "ok" if status == "可用" else "failed",
        "状态": status,
        "status": status,
        "失败原因": reason,
        "error": reason,
        "source_locator": _node_locator(node),
    }


def parse_source_page(
    page: int,
    html: str | None = None,
    items: list[Any] | None = None,
    selectors: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """解析单页，返回该页所有条目（含待补条目），位置从 1 开始保持原页顺序。"""
    selectors = selectors or {}
    records: list[dict[str, Any]] = []

    if items is not None:
        for index, item in enumerate(items, start=1):
            position = index
            if isinstance(item, dict):
                raw_pos, has_pos = _item_value(item, POSITION_KEYS)
                if has_pos:
                    digits = re.search(r"\d+", str(raw_pos))
                    if digits:
                        position = int(digits.group(0))
            records.append(_record_from_item(item, page, position))
        records = _dedupe_positions(records)
        return sorted(records, key=lambda row: int(row["页内位置"]))

    if not html:
        return []

    builder = _TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception:
        # HTMLParser 一般不会抛异常，但坏片段也不能阻断整批解析。
        pass

    nodes = _find_item_nodes(builder.root, selectors.get("item", ""))
    used: set[int] = set()
    for index, node in enumerate(nodes, start=1):
        position, _ = _position_from_node(node, index)
        while position in used:
            position += 1
        used.add(position)
        records.append(_record_from_html(node, page, position, selectors))
    return sorted(records, key=lambda row: int(row["页内位置"]))


def _dedupe_positions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: set[int] = set()
    for record in records:
        position = int(record["页内位置"])
        while position in used:
            position += 1
        used.add(position)
        record["页内位置"] = position
        record["位置"] = position
        record["position"] = position
        record["record_id"] = f"P{record['来源页']}-{position}"
    return records


# ---------------------------------------------------------------------------
# 请求参数归一化
# ---------------------------------------------------------------------------

PAGE_KEYS_IN = ("page", "page_no", "page_number", "页码", "来源页", "source_page", "sourcePage")
HTML_KEYS_IN = ("html", "content", "html_content", "htmlContent", "body", "raw", "页面内容")
ITEM_KEYS_IN = ("items", "records", "quotes", "报价", "条目", "rows")


def _page_number(value: Any, fallback: int) -> int:
    digits = re.search(r"\d+", str(value))
    return int(digits.group(0)) if digits else fallback


def normalize_source_pages(source_pages: Any) -> list[dict[str, Any]]:
    """把多页请求归一化成 ``[{page, title, html, items}]``。

    支持 ``[{"page": 4, "html": "..."}]``、``{"4": "<html>"}``、HTML 字符串列表
    以及直接携带 ``items`` 的结构化页面。
    """
    pages: list[dict[str, Any]] = []

    if source_pages is None:
        return pages

    if isinstance(source_pages, dict):
        if any(key in source_pages for key in PAGE_KEYS_IN + HTML_KEYS_IN + ITEM_KEYS_IN):
            source_pages = [source_pages]
        else:
            source_pages = [
                {"page": key, "html": value}
                for key, value in source_pages.items()
            ]

    if isinstance(source_pages, (str, bytes)):
        source_pages = [source_pages]

    if not isinstance(source_pages, list):
        return pages

    for index, raw in enumerate(source_pages, start=1):
        if isinstance(raw, str):
            pages.append({
                "page": index, "title": f"第{index}页", "html": raw, "items": None,
            })
            continue
        if isinstance(raw, (int, float)):
            pages.append({
                "page": int(raw), "title": f"第{int(raw)}页", "html": "", "items": None,
            })
            continue
        if not isinstance(raw, dict):
            continue

        page_no = index
        for key in PAGE_KEYS_IN:
            if key in raw and str(raw.get(key)).strip():
                page_no = _page_number(raw.get(key), index)
                break

        html = ""
        for key in HTML_KEYS_IN:
            if key in raw and raw.get(key) is not None:
                html = str(raw.get(key))
                break

        items = None
        for key in ITEM_KEYS_IN:
            value = raw.get(key)
            if isinstance(value, list):
                items = value
                break

        title = str(raw.get("title") or raw.get("标题") or f"第{page_no}页")
        pages.append({
            "page": page_no,
            "title": title,
            "html": html,
            "items": items,
        })

    # 相同页码以最后一次提交为准，保持提交顺序。
    by_page: dict[int, dict[str, Any]] = {}
    for item in pages:
        by_page[int(item["page"])] = item
    return sorted(by_page.values(), key=lambda item: int(item["page"]))


# ---------------------------------------------------------------------------
# 演示来源页（页面可多选提交；第 7 页故意带一条坏价格）
# ---------------------------------------------------------------------------

DEMO_SOURCE_PAGES: list[dict[str, Any]] = [
    {
        "page": 4,
        "title": "报价列表第4页",
        "html": """<ul class="quote-list">
  <li class="quote-item" data-position="1"><span class="product-name">机械键盘</span><span class="price">299</span></li>
  <li class="quote-item" data-position="2"><span class="product-name">无线鼠标</span><span class="price">89</span></li>
  <li class="quote-item" data-position="3"><span class="product-name">27寸显示器</span><span class="price">1299</span></li>
</ul>""",
    },
    {
        "page": 5,
        "title": "报价列表第5页",
        "html": """<div class="quote"><span class="product-name">USB-C 数据线</span><span class="price">29</span></div>
<div class="quote"><span class="product-name">桌面支架</span><span class="price">0</span></div>
<div class="quote"><span class="product-name">收纳包</span><span class="price">暂无报价</span></div>""",
    },
    {
        "page": 6,
        "title": "报价列表第6页",
        "html": """<ul class="quote-list">
  <li class="quote-item"><span class="product-name">蓝牙音箱</span><span class="price">199</span></li>
  <li class="quote-item"><span class="product-name">降噪耳机</span><span class="price">899</span></li>
</ul>""",
    },
    {
        "page": 7,
        "title": "报价列表第7页",
        "html": """<ul class="quote-list">
  <li class="quote-item" data-position="1"><span class="product-name">机械键盘</span><span class="price">329</span></li>
  <li class="quote-item" data-position="2"><span class="product-name">便携固态硬盘</span><span class="price">459</span></li>
  <li class="quote-item" data-position="3"><span class="product-name">USB集线器</span><span class="price">--</span></li>
</ul>""",
    },
    {
        "page": 8,
        "title": "报价列表第8页",
        "html": """<ul class="quote-list">
  <li class="quote-item"><span class="product-name">机械键盘</span><span class="price">309</span></li>
  <li class="quote-item"><span class="product-name">显示器</span><span class="price">￥1,199.00</span></li>
</ul>""",
    },
]


def demo_source_page_list() -> list[dict[str, Any]]:
    """给前端返回来源页清单，附上可解析条数和明显待补提示。"""
    result: list[dict[str, Any]] = []
    for page in DEMO_SOURCE_PAGES:
        records = parse_source_page(int(page["page"]), page["html"])
        result.append({
            "page": int(page["page"]),
            "title": page["title"],
            "html": page["html"],
            "count": len(records),
            "pending_count": sum(1 for row in records if row["status"] != "可用"),
            "preview": [row["商品"] for row in records[:5]],
        })
    return result
