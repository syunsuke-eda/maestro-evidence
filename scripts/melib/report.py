"""session.json から単一ファイルの index.html を作る。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .errors import MeError

MAX_ERROR_LINES = 200


def text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    return html.escape(str(value), quote=True)


def list_items(values: Any, empty: str = "なし") -> str:
    if not isinstance(values, list) or not values:
        return '<p class="muted">{0}</p>'.format(text(empty))
    return "<ul>" + "".join("<li>{0}</li>".format(text(value)) for value in values) + "</ul>"


def artifact_url(work: Path, relative: str) -> Optional[str]:
    """work dir 内の相対pathだけをリンクにする。外部URL・絶対path・.. は拒否。"""

    if not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MeError("成果物pathが work dir の外を指しています: {0}".format(relative))
    resolved = (work / candidate).resolve()
    try:
        resolved.relative_to(work.resolve())
    except ValueError as error:
        raise MeError("成果物pathが work dir の外を指しています: {0}".format(relative)) from error
    if not resolved.is_file():
        return None
    return quote(candidate.as_posix(), safe="/-_.")


def status_badge(status: Any) -> str:
    normalized = str(status or "unknown").lower()
    css = normalized if normalized in ("pass", "fail", "running") else "unknown"
    return '<span class="badge {0}">{1}</span>'.format(css, text(normalized.upper()))


def _figure(url: str, caption: str) -> str:
    return (
        '<figure><a href="{0}"><img src="{0}" loading="lazy" alt="{1}"></a>'
        "<figcaption>{1}</figcaption></figure>".format(url, text(caption))
    )


def render_steps(steps: List[Dict[str, Any]]) -> str:
    if not steps:
        return '<p class="muted">step なし</p>'
    rows = []
    for index, step in enumerate(steps, start=1):
        rows.append(
            "<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td>"
            "<td><pre>{4}</pre></td></tr>".format(
                index,
                text(step.get("label")),
                status_badge(step.get("status")),
                text(step.get("duration_seconds")),
                text(step.get("commands")),
            )
        )
    return (
        "<table><thead><tr><th>#</th><th>Label</th><th>結果</th><th>秒</th>"
        "<th>commands</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_plan(plan: Any) -> str:
    """cases.json（Phase 1 で承認した内容）があれば併記する。"""

    if not isinstance(plan, dict):
        return '<p class="muted">計画の記録なし</p>'
    return (
        "<dl><dt>前提</dt><dd>{0}</dd><dt>データ変更</dt><dd>{1}</dd></dl>"
        "<h5>手順</h5>{2}<h5>期待結果</h5>{3}".format(
            text(plan.get("precondition")),
            text(plan.get("mutations")),
            list_items(plan.get("steps")),
            list_items(plan.get("expected")),
        )
    )


def render_case(work: Path, case: Dict[str, Any]) -> str:
    case_id = str(case.get("id"))
    base = "cases/{0}".format(case_id)
    steps = case.get("steps") or []
    gallery: List[str] = []
    video = case.get("video") or {}
    for key, caption in (("contact_sheet", "動画のcontact sheet"), ("final_frame", "動画終端フレーム")):
        name = video.get(key)
        if not name:
            continue
        url = artifact_url(work, "{0}/{1}".format(base, name))
        if url:
            gallery.append(_figure(url, caption))
    for relative in case.get("screenshots") or []:
        url = artifact_url(work, str(relative))
        if url:
            gallery.append(_figure(url, Path(str(relative)).name))

    video_html = '<p class="muted">動画なし</p>'
    video_name = video.get("file")
    if video_name:
        url = artifact_url(work, "{0}/{1}".format(base, video_name))
        if url:
            video_html = (
                '<video controls preload="metadata" src="{0}"></video>'
                '<p><a href="{0}">動画を開く</a></p>'.format(url)
            )
    review = video.get("review") or {}
    errors = case.get("errors") or []
    error_block = (
        '<p class="muted">error行なし</p>'
        if not errors
        else "<pre>{0}</pre>".format(text("\n".join(errors[:MAX_ERROR_LINES])))
    )
    visual = case.get("review") or {}
    return """
    <article class="case-card">
      <div class="case-heading"><div><p class="case-id">{case_id}</p><h3>{title}</h3></div>{badge}</div>
      <div class="two-column">
        <section><h4>実行</h4><dl>
          <dt>開始</dt><dd>{started}</dd><dt>終了</dt><dd>{ended}</dd>
          <dt>step 数</dt><dd>{step_count}</dd><dt>error 行</dt><dd>{error_count}</dd>
          <dt>所見</dt><dd>{notes}</dd></dl></section>
        <section><h4>動画</h4><dl>
          <dt>状態</dt><dd>{video_status}</dd>
          <dt>codec</dt><dd>{codec} / {pixel_format}</dd>
          <dt>size</dt><dd>{size} bytes</dd>
          <dt>SHA-256</dt><dd>{sha}</dd>
          <dt>先頭静止の切り詰め</dt><dd>{trim}秒 / {trim_status}</dd></dl></section>
      </div>
      <section><h4>承認済みの計画</h4>{plan}</section>
      <section><h4>目視レビュー</h4><dl><dt>結果</dt><dd>{visual_status}</dd></dl><p>{visual_notes}</p></section>
      <section><h4>Steps</h4>{steps}</section>
      <section><h4>画面録画</h4>{video}</section>
      <section><h4>画像</h4><div class="gallery">{gallery}</div></section>
      <section><h4>error 行</h4>{errors}</section>
    </article>
    """.format(
        case_id=text(case_id),
        title=text(case.get("title")),
        badge=status_badge(case.get("status")),
        started=text(case.get("started_at")),
        ended=text(case.get("ended_at")),
        step_count=text(len(steps)),
        error_count=text(case.get("errors_count", len(errors))),
        notes=text(case.get("notes")),
        video_status=text(video.get("status")),
        codec=text(review.get("codec")),
        pixel_format=text(review.get("pixel_format")),
        size=text(review.get("size_bytes")),
        sha=text(review.get("sha256")),
        trim=text(video.get("trimmed_leading_seconds")),
        trim_status=text(video.get("trim_status")),
        plan=render_plan(case.get("plan")),
        visual_status=text(visual.get("status")),
        visual_notes=text(visual.get("notes")),
        steps=render_steps(steps),
        video=video_html,
        gallery="".join(gallery) or '<p class="muted">画像なし</p>',
        errors=error_block,
    )


def build_html(session: Dict[str, Any], work: Path) -> str:
    fingerprint = session.get("fingerprint") or {}
    device = session.get("device") or {}
    config = session.get("config") or {}
    secret_scan = session.get("secret_scan") or {}
    violations = secret_scan.get("violations") or []
    secret_summary = "clean" if not violations else "{0}件を[REDACTED]へ置換".format(len(violations))
    cases = "".join(
        render_case(work, case) for case in session.get("cases", []) if isinstance(case, dict)
    )
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maestro Evidence — {branch}</title>
<style>
:root{{--bg:#f5f7fb;--card:#fff;--ink:#18212f;--muted:#64748b;--line:#dbe2ea;--accent:#3157d5;--pass:#147a4b;--fail:#b42318;--running:#9a6700}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{width:min(1120px,calc(100% - 32px));margin:32px auto 80px}}header,.panel,.case-card{{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 18px #24324a0c}}
header{{padding:28px;border-top:5px solid var(--accent)}}h1,h2,h3,h4,p{{margin-top:0}}h1{{margin-bottom:6px}}h2{{margin-top:32px}}h3{{margin-bottom:2px}}h4{{margin-bottom:8px}}.panel,.case-card{{padding:22px;margin-top:16px}}
.summary-grid,.two-column{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 24px}}dl{{display:grid;grid-template-columns:minmax(120px,auto) 1fr;gap:6px 14px;margin:0}}dt{{font-weight:700}}dd{{margin:0;overflow-wrap:anywhere}}
.badge{{display:inline-block;border-radius:999px;padding:5px 11px;font-weight:800;letter-spacing:.04em;color:#fff}}.badge.pass{{background:var(--pass)}}.badge.fail{{background:var(--fail)}}.badge.running{{background:var(--running)}}.badge.unknown{{background:var(--muted)}}
.case-heading{{display:flex;align-items:start;justify-content:space-between;gap:16px}}.case-id{{margin:0;color:var(--accent);font:700 12px/1.2 ui-monospace,SFMono-Regular,monospace}}.muted{{color:var(--muted)}}ul{{padding-left:22px;margin:0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}pre{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,SFMono-Regular,monospace}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}figure{{margin:0}}img,video{{display:block;width:100%;max-height:620px;object-fit:contain;background:#10151d;border-radius:10px;border:1px solid var(--line)}}figcaption{{margin-top:5px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}}a{{color:var(--accent)}}
@media(max-width:720px){{.summary-grid,.two-column{{grid-template-columns:1fr}}main{{width:min(100% - 18px,1120px);margin-top:10px}}header,.panel,.case-card{{padding:16px}}}}
</style>
</head>
<body><main>
<header><p class="case-id">MAESTRO EVIDENCE</p><h1>{branch}</h1><p>{app_id} / {platform} / {device_name}</p></header>
<h2>実行概要</h2>
<section class="panel summary-grid">
<dl><dt>Base</dt><dd>{base_ref}</dd><dt>Merge base</dt><dd>{merge_base}</dd><dt>HEAD</dt><dd>{head_sha}</dd><dt>Diff SHA-256</dt><dd>{diff_sha}</dd></dl>
<dl><dt>Device</dt><dd>{device_name} / {device_id}</dd><dt>開始</dt><dd>{started}</dd><dt>終了</dt><dd>{finished}</dd><dt>Secret scan</dt><dd>{secret}</dd></dl>
</section>
<h2>変更ファイル</h2><section class="panel">{changed}</section>
<h2>確認ケース</h2>{cases}
</main></body></html>""".format(
        branch=text(fingerprint.get("branch")),
        app_id=text(config.get("app_id")),
        platform=text(config.get("platform")),
        device_name=text(device.get("name")),
        device_id=text(device.get("id")),
        base_ref=text(fingerprint.get("base_ref")),
        merge_base=text(fingerprint.get("merge_base")),
        head_sha=text(fingerprint.get("head_sha")),
        diff_sha=text(fingerprint.get("diff_sha256")),
        started=text(session.get("started_at")),
        finished=text(session.get("finished_at")),
        secret=text(secret_summary),
        changed=list_items(fingerprint.get("changed_files"), "変更なし"),
        cases=cases or '<section class="panel"><p class="muted">ケースなし</p></section>',
    )


def write_report(session: Dict[str, Any], work: Path) -> Path:
    output = work / "index.html"
    output.write_text(build_html(session, work), encoding="utf-8")
    return output
