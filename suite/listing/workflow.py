"""Per-user workflow recipes; only confirmed outcomes become reusable memory."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .models import ListingError
from .page_ai import FORBIDDEN

DESCRIBE = r"""e => {
 const p=e.closest('button,a,[role=button],[role=menuitem],[role=tab]')||e;
 const attrs={};
 for(const name of ['id','data-testid','data-test-id','aria-label','title','role','name','type']){
   const v=p.getAttribute(name);
   if(v && v.length<140 && !/^(gl|rc_select_|:r)|[a-f0-9]{24,}/i.test(v)) attrs[name]=v;
 }
 let node=p, parts=[];
 for(let n=0;node && node!==document.body && n<5;node=node.parentElement,n++){
   const tag=node.tagName.toLowerCase();
   const siblings=[...node.parentElement.children].filter(x=>x.tagName===node.tagName);
   parts.unshift(tag+':nth-of-type('+(siblings.indexOf(node)+1)+')');
 }
 const text=p.matches('input,textarea')?'':(p.innerText||'').replace(/\s+/g,' ').trim().slice(0,140);
 return {tag:p.tagName.toLowerCase(),text,attrs,css:parts.join(' > ')};
}"""


def page_key(page):
    parsed = urlparse(page.url)
    path = re.sub(r"[a-f0-9]{24,}|\d{5,}", "{id}", parsed.path, flags=re.I)
    return parsed.hostname + path if parsed.hostname else path


def scope_key(root):
    if root is None:
        return "page"
    # Labels only: do not retain current SKU, filenames, input contents or prices.
    return (
        root.evaluate(
            "e=>[...e.querySelectorAll('label,h1,h2,h3,h4')].map(x=>x.innerText.trim()).filter(Boolean).slice(0,4).join('/')"
        )[:120]
        or "field"
    )


def safe_target(target, allow_save=False):
    actual = target.evaluate(
        "e=>{const p=e.closest('button,a,[role=button],[role=menuitem]')||e;return [p.innerText,p.getAttribute('aria-label'),p.title].filter(Boolean).join(' ')}"
    )
    actual = re.sub(r"\s+", "", actual)
    if FORBIDDEN.search(actual) or ("保存草稿" in actual and not allow_save):
        raise ListingError("该位置涉及发布、上下架、删除商品或提前保存，未执行。")


def semantic_text(value):
    text = re.sub(r"[\s⌄∨▼▾：:·]+", "", value).casefold()
    for names in (
        ("新建商品", "创建商品", "新增商品", "创建货品"),
        ("单商品", "单个商品", "独立商品", "单品商品"),
    ):
        if text in names:
            return names[0]
    return text


def resolve(page, descriptor, root=None, allow_save=False):
    """Use semantic selectors first; a CSS fallback must still match identity."""
    if not isinstance(descriptor, dict):
        return None
    scope = root or page
    tag, text, attrs = (
        descriptor.get("tag"),
        descriptor.get("text", ""),
        descriptor.get("attrs", {}),
    )
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,25}", tag or "") or not isinstance(
        attrs, dict
    ):
        return None
    choices = []
    for attr in ("data-testid", "data-test-id", "id", "aria-label", "name", "title"):
        value = attrs.get(attr)
        if isinstance(value, str) and value:
            choices.append(
                scope.locator(tag + "[" + attr + "=" + json.dumps(value) + "]")
            )
    if text:
        choices.append(scope.get_by_text(text, exact=True))
        # Match known equivalent labels despite arrows/spacing or a new DOM path.
        labels = scope.locator(tag).evaluate_all(
            "nodes=>nodes.map((e,i)=>({i,text:(e.innerText||'').trim(),visible:!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'}))"
        )
        equivalent = [
            row["i"]
            for row in labels
            if row["visible"] and semantic_text(row["text"]) == semantic_text(text)
        ]
        if len(equivalent) == 1:
            choices.append(scope.locator(tag).nth(equivalent[0]))
    css = descriptor.get("css", "")
    if re.fullmatch(r"[a-z0-9:()>\-\s]+", css or ""):
        choices.append(scope.locator(css))
    for query in choices:
        visible = [
            query.nth(i) for i in range(query.count()) if query.nth(i).is_visible()
        ]
        if len(visible) != 1:
            continue
        target = visible[0]
        current = target.evaluate(DESCRIBE)
        # Never replay an old position when its button has changed meaning.
        matches = (
            semantic_text(current["text"]) == semantic_text(text)
            if text
            else any(
                current["attrs"].get(key) == value
                for key, value in attrs.items()
                if key
                in ("id", "data-testid", "data-test-id", "aria-label", "name", "title")
            )
        )
        if not matches or current["tag"] != tag:
            continue
        safe_target(target, allow_save)
        return target
    return None


class WorkflowStore:
    def __init__(self, path, profile="默认配置"):
        self.path, self.profile = Path(path), profile
        self.lock = threading.RLock()
        self.data = {"version": 1, "profiles": {}}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if value.get("version") != 1 or not isinstance(
                    value.get("profiles"), dict
                ):
                    raise ValueError()
                self.data = value
            except (OSError, ValueError, AttributeError):
                raise ListingError(
                    f"流程配置无法读取，请备份后移走损坏文件再重新打开：{self.path}"
                ) from None
        self.data["profiles"].setdefault(profile, {"steps": {}})

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)

    def profiles(self):
        return list(self.data["profiles"])

    def steps(self):
        return self.data["profiles"][self.profile]["steps"]

    @staticmethod
    def key(goal, page, scope="page"):
        return hashlib.sha256(
            json.dumps([goal, page, scope], ensure_ascii=False).encode()
        ).hexdigest()[:24]

    def recipes(self, goal, page, scope="page"):
        return sorted(
            self.steps().get(self.key(goal, page, scope), []),
            key=lambda r: (r.get("failures", 0), -r.get("updated", 0)),
        )

    def hints(self, goal, page, scope="page"):
        """Related successes are context for AI, never permission to replay blindly."""
        records = [
            r
            for rows in self.steps().values()
            for r in rows
            if r.get("goal") == goal
            and r.get("scope", "page") == scope
            and r.get("page", "").split("/", 1)[0] == page.split("/", 1)[0]
        ]
        records.sort(key=lambda r: (r.get("failures", 0), -r.get("updated", 0)))
        return [
            {
                "目标": r["goal"],
                "适用页面": r["page"],
                "动作": [
                    {"动作": a["kind"], "控件": a.get("target", {}).get("text", "")}
                    for a in r["actions"]
                ],
                "结果": r.get("outcome", ""),
                "失效次数": r.get("failures", 0),
            }
            for r in records[:3]
        ]

    def remember(self, goal, page, actions, scope="page", source="程序", outcome=""):
        if not actions or len(actions) > 24:
            return
        with self.lock:
            key = self.key(goal, page, scope)
            rows = self.steps().setdefault(key, [])
            previous = next((r for r in rows if r["actions"] == actions), None)
            record = previous or {
                "goal": goal,
                "page": page,
                "scope": scope,
                "actions": actions,
                "successes": 0,
            }
            record.update(
                source=source,
                outcome=outcome,
                successes=record["successes"] + 1,
                failures=0,
                updated=time.time(),
            )
            if previous is None:
                rows.insert(0, record)
            del rows[4:]
            self.save()

    def failed(self, recipe):
        with self.lock:
            recipe["failures"] = recipe.get("failures", 0) + 1
            self.save()


def replay(
    page, actions, root=None, allow_save=False, cancel=None, observe=None, done=None
):
    """Replay a verified recipe; callers must check its expected page result."""
    for index, action in enumerate(actions):
        if done and done():
            return True
        if observe:
            page = observe() or page
        if cancel and cancel.is_set():
            raise ListingError("已停止流程回放")
        kind = action.get("kind")
        if kind == "scroll":
            page.mouse.wheel(0, max(-700, min(700, int(action.get("dy", 0)))))
            page.wait_for_timeout(150)
            continue
        target = resolve(page, action.get("target"), root, allow_save)
        if target is None:
            # A submenu may already be open: skip obsolete preparation only
            # when the very next expected control is currently visible.
            if kind in ("hover", "click") and index + 1 < len(actions):
                following = actions[index + 1]
                if (
                    following.get("kind") in ("target", "click")
                    and resolve(page, following.get("target"), root, allow_save)
                    is not None
                ):
                    continue
            return None
        if kind == "target":
            return target
        if kind == "hover":
            target.hover(timeout=4000)
            page.wait_for_timeout(150)
        elif kind == "click":
            target.click(timeout=5000)
        else:
            return None
    return True
