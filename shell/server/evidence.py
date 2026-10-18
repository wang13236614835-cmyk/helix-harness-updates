"""0.5 证据核验：来源引用必须“对得上”，结论不能越过主张上限。

每条引用分开记录几个维度（checks），不再合成一个“强/弱”：
- format  ：写法是否合法；
- record  ：记录是否存在——本项目已登记文件 / 只读见证文件指纹一致为 True；DOI/PMID 离线无法核实为 None；
- readable：本地文件能否解析成文本（只在填了摘录时检查）；
- quote   ：摘录是否逐字出现在文件里（found / missing / unverified）。
文件指纹只证明“是这份文件”，不证明内容支持这条主张——那仍由两位真人判断。

等级（level）：
- verified   ：本项目已登记文件 / 只读见证文件，指纹一致（strong=True）；
- format_only：DOI / PMID / PMCID 只校验了格式，离线未联网核实（strong=False）；
- supplement ：http(s) 链接，只能作补充；
- 其余（随手写的文字、对不上任何文件的哈希）直接拒绝。

项目证据规则：PASS / REJECTED / OUT_OF_DOMAIN 至少要一条 verified 来源；
只有 DOI/PMID 时，必须由真人明确勾选“已打开原文核对”（id_checked），账本会记为“真人核对，未联网”。
摘录：所选文件都无法解析时不拒绝，但标为“尚未核验”，不会显示成已核验。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

STRONG_DECISIONS = {"PASS", "REJECTED", "OUT_OF_DOMAIN"}
_HEX = re.compile(r"(?:sha256:)?\s*([0-9a-f]{12,64})\b", re.I)
_DOI = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:<>A-Za-z0-9]+)$", re.I)
_PMID = re.compile(r"^(?:pmid:?\s*)(\d{4,9})$", re.I)
_PMCID = re.compile(r"^(?:pmcid:?\s*)?(PMC\d{4,9})$", re.I)
_URL = re.compile(r"^https?://[^\s]{4,}$", re.I)
TEXT_EXT = {".txt", ".csv", ".tsv", ".md", ".json", ".jsonl", ".sdf", ".smi", ".smiles", ".mol", ".mol2", ".pdb", ".fasta", ".fa", ".log", ".yaml", ".yml", ".xml", ".html", ".htm", ".tex", ".bib", ".ris"}


class EvidenceError(ValueError):
    pass


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = re.sub(r"[\"'“”‘’`]", "", text)
    text = re.sub(r"[,;|\t]+", " ", text)  # 表格行：分隔符当空白，选中整行也能逐字核对
    return re.sub(r"\s+", " ", text).strip()


def read_text(path: Path, limit: int = 3_000_000) -> str | None:
    """尽量把本地文件变成纯文本；不支持的格式返回 None（不能做摘录校验）。"""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            raw = path.read_bytes()[:limit]
            for enc in ("utf-8-sig", "gb18030", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                return None
            out = []
            for page in PdfReader(str(path)).pages[:80]:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) > limit:
                    break
            return "\n".join(out)
        if ext in {".xlsx", ".xlsm"}:
            try:
                import openpyxl  # type: ignore
            except Exception:
                return None
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:8]:
                for row in ws.iter_rows(values_only=True):
                    out.append("\t".join("" if v is None else str(v) for v in row))
                    if len(out) > 50_000:
                        break
            return "\n".join(out)
        if ext == ".docx":
            try:
                import docx  # type: ignore
            except Exception:
                return None
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    except Exception:
        return None
    return None


def quote_in(quote: str, text: str | None) -> bool:
    q = _norm(quote)
    return bool(q) and text is not None and q in _norm(text)


def _row(raw: str, kind: str, level: str, **extra) -> dict:
    ok = level != "invalid"
    record = True if level == "verified" else (None if level in ("format_only", "supplement") else False)
    return {"raw": raw, "kind": kind, "level": level, "strong": level == "verified", "ok": ok,
            "checks": {"format": ok or kind == "hash", "record": record, "readable": None, "quote": None}, **extra}


def resolve(ref: str, files: Iterable[Mapping[str, Any]], witnesses: Iterable[Mapping[str, Any]] = ()) -> dict:
    raw = str(ref or "").strip()[:500]
    files, witnesses = list(files), list(witnesses)
    head = raw.split("#", 1)[0].strip()
    m = _HEX.match(head)
    if m:
        h = m.group(1).lower()
        for f in files:
            if str(f.get("sha256", "")).lower().startswith(h):
                return _row(raw, "file", "verified", sha256=f["sha256"], name=f.get("name"), stored=f.get("stored"), note="本项目已登记文件，指纹一致")
        for w in witnesses:
            if str(w.get("sha256", "")).lower().startswith(h) and w.get("found"):
                return _row(raw, "witness", "verified", sha256=w["sha256"], name=w.get("label"), note="只读来源见证文件，指纹一致")
        return _row(raw, "hash", "invalid", note="这个 SHA 对不上本项目任何已登记文件")
    for f in files:
        if head and head in (f.get("name"), f.get("stored"), "files/" + str(f.get("stored"))):
            return _row(raw, "file", "verified", sha256=f["sha256"], name=f.get("name"), stored=f.get("stored"), note="按文件名匹配到已登记文件")
    for w in witnesses:
        if head and w.get("found") and head in (w.get("label"), w.get("path")):
            return _row(raw, "witness", "verified", sha256=w.get("sha256"), name=w.get("label"), note="按路径匹配到只读见证文件")
    for rx, kind in ((_DOI, "doi"), (_PMID, "pmid"), (_PMCID, "pmcid")):
        m = rx.match(head)
        if m:
            return _row(raw, kind, "format_only", id=m.group(1), note="格式合法，离线未联网核实")
    if _URL.match(head):
        return _row(raw, "url", "supplement", note="网页链接：只能作补充")
    return _row(raw, "unknown", "invalid", note="认不出这条引用：请用已登记文件、DOI:10.xxxx/…、PMID:12345678 或 PMC1234567")


def check_refs(refs: list[str], *, decision: str, files, witnesses=(), quote: str = "", file_text=None, id_checked: bool = False) -> list[dict]:
    """返回解析后的引用；不合格直接抛 EvidenceError。file_text(row) -> str|None 用于摘录校验。
    id_checked：真人明确声明已打开 DOI/PMID 对应原文核对过（离线无法替他核实）。"""
    rows = [resolve(r, files, witnesses) for r in refs if str(r).strip()]
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise EvidenceError("来源引用对不上：" + "；".join(f"“{r['raw'][:60]}” {r['note']}" for r in bad[:4]))
    if decision in STRONG_DECISIONS and not any(r["strong"] for r in rows):
        ids = [r for r in rows if r["level"] == "format_only"]
        if not ids:
            raise EvidenceError(f"结论 {decision} 至少需要一条可核验的来源：本项目已登记文件（SHA-256），或由你核对过原文的 DOI / PMID。网页链接只能作补充。")
        if not id_checked:
            raise EvidenceError(f"结论 {decision} 只引用了 DOI / PMID，Helix 离线只能确认格式合法、不能确认文献存在或支持这条结论。"
                                "请上传原文作为本项目文件，或勾选“我已打开这条 DOI / PMID 的原文核对过”。")
        for r in ids:
            r["level"], r["checks"]["record"], r["note"] = "human_checked", "human", "格式合法；真人声明已核对原文（未联网核实）"
    quote = str(quote or "").strip()
    if quote:
        if len(quote) < 6:
            raise EvidenceError("原文摘录太短（至少 6 个字）。")
        local = [r for r in rows if r["kind"] in ("file", "witness")]
        if not local:
            raise EvidenceError("填写了原文摘录，但没有选本地文件来源；摘录只能对照本地文件核验。")
        checked = False
        for r in local:
            text = file_text(r) if file_text else None
            if text is None:
                r["quote"] = r["checks"]["quote"] = "unverified"
                r["checks"]["readable"] = False
                r["note"] += "；文件无法解析，摘录尚未核验"
                continue
            checked = True
            r["checks"]["readable"] = True
            r["quote"] = r["checks"]["quote"] = "found" if quote_in(quote, text) else "missing"
        if checked and not any(r.get("quote") == "found" for r in local):
            raise EvidenceError("原文摘录在所选文件里找不到（逐字比对，忽略空白和引号）。请从原文复制。")
    return rows


def ceiling_guard(text: str, *, ceiling: str = "", proofread=None) -> list[str]:
    """真人改写/结论文字也要过主张上限与红线；返回拦截原因（空=通过）。"""
    if not str(text or "").strip() or proofread is None:
        return []
    r = proofread({"text": text}, snapshot={"research_context": {"v4_claim_ceiling": ceiling}}, mismatches=[])
    return [n for n in r["notes"] if not n.startswith(("无来源", "来源字段"))] if r["block"] or any(n.startswith("主张上限") for n in r["notes"]) else []


__all__ = ["check_refs", "resolve", "read_text", "quote_in", "ceiling_guard", "EvidenceError", "STRONG_DECISIONS"]
