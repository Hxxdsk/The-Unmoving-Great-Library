"""最新标准需 Google Search grounding 与官方出处；不从模型记忆声称‘最新’。"""

from datetime import date
import json
import re
import urllib.parse
import urllib.request

from color_tool.gemini import GeminiError
from .cache import ResultCache


def official(url):
    if not isinstance(url, str):
        return False
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and (
            parsed.hostname == "samr.gov.cn" or parsed.hostname.endswith(".samr.gov.cn")
        )
    )


def compact(text):
    return re.sub(r"[\s\u3000]", "", text).replace("－", "-").upper()


def validate_standard(result, metadata, fetch=None):
    if not isinstance(result, dict) or result.get("status") != "现行":
        raise GeminiError("未找到现行标准证据。")
    code = result.get("execution_standard", "")
    if not isinstance(code, str) or not re.fullmatch(
        r"(?:GB(?:/T)?|FZ/T)\s*\d+(?:\.\d+)?-\d{4}", code
    ):
        raise GeminiError("执行标准格式不受支持。")
    try:
        effective = date.fromisoformat(result.get("effective_date", ""))
    except (ValueError, TypeError):
        raise GeminiError("标准实施日期未确认。") from None
    if effective > date.today():
        raise GeminiError("标准尚未实施，不能当作当前要求。")
    if not isinstance(metadata, dict):
        raise GeminiError("缺少联网检索证据。")
    sources = metadata.get("groundingChunks", [])
    if (
        not isinstance(sources, list)
        or not isinstance(metadata.get("groundingSupports"), list)
        or not metadata["groundingSupports"]
        or not sources
    ):
        raise GeminiError("当前模型没有提供联网检索证据。")
    cited = set()
    for support in metadata["groundingSupports"]:
        if isinstance(support, dict):
            cited.update(
                index
                for index in support.get("groundingChunkIndices", [])
                if type(index) is int
            )
    security = result.get("safety_category", "")
    if security and (
        not isinstance(security, str)
        or not re.fullmatch(r"GB\s*18401-\d{4}\s*[ABC]类", security)
    ):
        security = ""
    try:
        safety_effective = date.fromisoformat(result.get("safety_effective_date", ""))
        safety_current = (
            result.get("safety_status") == "现行" and safety_effective <= date.today()
        )
    except (ValueError, TypeError):
        safety_current = False
    safety_code = (
        re.sub(r"\s*[ABC]类$", "", security) if security and safety_current else ""
    )
    verified, safety_sources = [], []
    for index in sorted(cited):
        if not 0 <= index < len(sources):
            continue
        source = sources[index]
        web = source.get("web", {}) if isinstance(source, dict) else {}
        if not isinstance(web, dict):
            continue
        url, title = web.get("uri", ""), web.get("title", "")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        # 搜索可能返回 Google 引用重定向。只解析引用本身，不采信模型自行拼的 URL。
        parsed = urllib.parse.urlparse(url)
        redirected = (
            parsed.scheme == "https"
            and parsed.hostname == "vertexaisearch.cloud.google.com"
        )
        body = ""
        wanted = [code] + ([safety_code] if safety_code else [])
        if redirected or (
            official(url)
            and not any(
                compact(wanted_code) in compact(title) for wanted_code in wanted
            )
        ):
            try:
                if fetch:
                    url, body = fetch(url)
                else:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "GreatLibraryToolbox/2.0"}
                    )
                    with urllib.request.urlopen(request, timeout=15) as response:
                        url = response.geturl()
                        raw = response.read(1024 * 1024)
                        body = raw.decode("utf-8", errors="replace")
            except (OSError, ValueError):
                continue
        if official(url) and compact(code) in compact(title + body):
            verified.append(url)
        if (
            official(url)
            and safety_code
            and compact(safety_code) in compact(title + body)
        ):
            safety_sources.append(url)
    if not verified:
        raise GeminiError("标准没有可核对的官方出处，已留空。")
    return {
        "执行标准(*)": code,
        "安全技术级别(*)": security if safety_sources else "",
        "标准来源": list(dict.fromkeys(verified + safety_sources)),
        "标准实施日期": effective.isoformat(),
        "标准依据": (
            result.get("reason", "")
            if isinstance(result.get("reason", ""), str)
            else ""
        ),
        "标准核对日期": date.today().isoformat(),
        "标准备注": "现行标准检索建议；适用范围及接触类别请核对。",
    }


def lookup_standard(client, garment, warnings, cache=None):
    cache = cache or ResultCache("standards-v1")
    key = cache.key(
        {"date": date.today().isoformat(), "model": client.model, "garment": garment}
    )
    cached = cache.get(key)
    if (
        isinstance(cached, dict)
        and cached.get("标准核对日期") == date.today().isoformat()
        and all(official(url) for url in cached.get("标准来源", []))
        and cached.get("标准来源")
    ):
        return cached
    prompt = (
        f"今天是 {date.today().isoformat()}。通过 Google 搜索国家市场监管总局及全国标准信息公共服务平台，"
        "查找此服装当前适用的最新、已实施、现行产品执行标准和 GB 18401 安全技术类别。"
        "区分针织/梭织、羽绒/棉填充及穿着接触条件；图片不能确定适用条件时不得猜测。"
        "排除废止、被替代或尚未实施的标准。必须引用 std.samr.gov.cn、openstd.samr.gov.cn 或 samr.gov.cn 的证据。"
        "安全类别需独立检索其基础标准，不能单凭产品标准推断。仅输出 JSON 对象："
        "execution_standard（GB/T...或FZ/T...）、safety_category（如GB 18401-2010 B类，条件不明为空）、"
        "status（现行或无法确认）、effective_date（YYYY-MM-DD）、safety_status、safety_effective_date、reason。不要附 JSON 外文本。服装数据："
        + json.dumps(garment, ensure_ascii=False)
    )
    try:
        result, metadata = client.grounded(prompt)
        verified = validate_standard(result, metadata)
        cache.put(key, verified)
        return verified
    except GeminiError as exc:
        client.check_cancel()
        warnings.append(f"最新标准未确认：{exc}；对应未匹配字段留空。")
        return {}
