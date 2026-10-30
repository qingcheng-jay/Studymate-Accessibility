"""StudyMate AI — Streamlit 前端入口（单进程直连后端模块，无需 uvicorn）。

启动：
    streamlit run app.py
然后浏览器打开 http://localhost:8501

结构：侧边栏 6 个页面，数据全部直接调用工程内的 Python 模块
（agent / rag / database / model / utils），不走 HTTP。
"""
import re
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# 从 .env 加载 API Key（禁止硬编码）
load_dotenv(PROJECT_ROOT / ".env")

import streamlit as st  # noqa: E402

from agent.react_agent import run_agent  # noqa: E402
from model.factory import LLMCallError, LLMConfigError  # noqa: E402
from database import repo  # noqa: E402
from database.db import init_db, session_scope  # noqa: E402
from rag.backfill import backfill_index  # noqa: E402
from rag.vector_store import get_vector_store  # noqa: E402
from utils.file_handler import pdf_loader  # noqa: E402
from utils.accessibility import notify, render_accessibility  # noqa: E402

st.set_page_config(page_title="StudyMate AI", page_icon="🎓", layout="wide")

PAGES = ["仪表盘", "AI 对话", "学习计划", "学习任务", "知识库", "学习统计"]

MAX_SIZE = 20 * 1024 * 1024  # 上传大小上限 20MB
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"


@st.cache_resource  #结果备忘录
def _bootstrap() -> bool:
    """进程内只执行一次：建表 + 回填向量库（均幂等）。"""
    init_db()
    backfill_index()
    return True


_bootstrap()


# ---------- 通用小工具 ----------


def confirm_button(label: str, key: str) -> bool:
    """两段式确认按钮（替代原前端的 window.confirm）：第一次点进入确认态，第二次才执行。"""
    if st.session_state.get("confirm_key") == key:
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button(f"确认{label}", key=key + "_go", type="primary"):
                st.session_state["confirm_key"] = None
                return True
        with c2:
            if st.button(f"取消{label}", key=key + "_no"):
                st.session_state["confirm_key"] = None
                notify(f"已取消{label}。", key)
                st.rerun()
        return False
    if st.button(label, key=key):
        st.session_state["confirm_key"] = key
        notify(f"请确认{label}；也可以选择取消。", key + "_go")
        st.rerun()
    return False


def config_warnings() -> None:
    """侧边栏配置自检：LLM / Embedding Key 缺失时给出提示。"""
    import os

    if not os.getenv("LLM_API_KEY", "").strip() or not os.getenv("LLM_BASE_URL", "").strip():
        st.sidebar.warning("未配置 LLM_API_KEY / LLM_BASE_URL，AI 对话不可用")
    if not os.getenv("DASHSCOPE_API_KEY", "").strip():
        st.sidebar.warning("未配置 DASHSCOPE_API_KEY，知识库向量化不可用")


# ---------- 页面：仪表盘 ----------


def task_row(t: dict, prefix: str) -> None:
    """一行任务：勾选框 + 标题 + 截止日 + 删除（任务页与仪表盘共用）。"""
    done = t["status"] == "completed"
    c1, c3 = st.columns([3, 1])
    with c1:
        if st.checkbox(f"完成任务：{t['title']}（任务 #{t['id']}）", value=done, key=f"{prefix}_chk{t['id']}"):
            if not done:
                _set_task_status(t["id"], "completed", f"任务“{t['title']}”已完成。", f"{prefix}_chk{t['id']}")
        elif done:
            _set_task_status(t["id"], "pending", f"任务“{t['title']}”已设为待完成。", f"{prefix}_chk{t['id']}")
    with c3:
        badge = "✅ 已完成" if done else "⏳ 待完成"
        due = f" · 截止 {t['due_date']}" if t.get("due_date") else ""
        st.caption(f"{badge}{due}")
        if confirm_button(f"删除任务：{t['title']}（任务 #{t['id']}）", f"{prefix}_del{t['id']}"):
            with session_scope() as db:
                repo.delete_task(db, t["id"])
            notify(f"已删除任务“{t['title']}”。")
            st.rerun()


def _set_task_status(task_id: int, status: str, message: str, focus: str) -> None:
    with session_scope() as db:
        repo.update_task(db, task_id, {"status": status})
    notify(message, focus)
    st.rerun()


def page_dashboard() -> None:
    st.title("📊 仪表盘")
    with session_scope() as db:
        stats = repo.get_statistics(db)
        today_tasks = repo.tasks_of_today(db)
        plans = repo.list_plans(db)

    c1, c2, c3 = st.columns(3)
    c1.metric("今日任务", f"{len(today_tasks)} 项")
    c2.metric("任务完成率", stats["completion_rate"])
    c3.metric("当前计划", plans[0]["topic"] if plans else "—")

    left, right = st.columns(2)
    with left:
        st.subheader("今日任务")
        if not today_tasks:
            st.info("今天还没有任务，去「学习任务」新建，或让 AI 帮你安排。")
        for t in today_tasks[:6]:
            task_row(t, "dash")
        if len(today_tasks) > 6:
            st.caption(f"…还有 {len(today_tasks) - 6} 项")
    with right:
        st.subheader("当前计划阶段")
        if not plans:
            st.info("还没有学习计划，对 AI 说「帮我制定 30 天学习计划」即可生成。")
        else:
            _stage_table(plans[0])


def _stage_table(plan: dict, show_title: bool = True) -> None:
    """计划的阶段表：时间 / 阶段 / 天数 / 学习重点。"""
    stages = plan.get("stages") or []
    if not stages:
        st.caption("该计划没有阶段明细。")
        return
    rows = [
        {
            "时间": s.get("range", ""),
            "阶段": s.get("name", ""),
            "天数": s.get("days", 0),
            "学习重点": s.get("focus", ""),
        }
        for s in stages
    ]
    if show_title:
        st.markdown(f"**{plan['topic']}**（{plan['days']} 天）")
    st.table(rows)


# ---------- 页面：AI 对话 ----------


def page_chat() -> None:
    st.title("💬 AI 学习对话")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []  # [{role, content, trace?}]
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = None

    # 工具栏：新对话 / 历史会话
    b1, b2 = st.columns([1, 3])
    with b1:
        if st.button("新对话", key="new_chat"):
            st.session_state["messages"] = []
            st.session_state["session_id"] = None
            notify("已开始新对话。", "chat_prompt")
            st.rerun()
    with b2:
        with st.expander("🕐 历史会话"):
            with session_scope() as db:
                sessions = repo.list_chat_sessions(db)
            if not sessions:
                st.caption("还没有历史会话。")
            for s in sessions:
                r1, r2 = st.columns([4, 1])
                with r1:
                    if st.button(
                        f"打开会话：{s['title']}（会话 #{s['id']}，{s['updated_at'][:16]}）",
                        key=f"load_s{s['id']}",
                        use_container_width=True,
                    ):
                        _load_session(s["id"])
                with r2:
                    if confirm_button(f"删除会话：{s['title']}（会话 #{s['id']}）", f"del_s{s['id']}"):
                        with session_scope() as db:
                            repo.delete_chat_session(db, s["id"])
                        if st.session_state.get("session_id") == s["id"]:
                            st.session_state["messages"] = []
                            st.session_state["session_id"] = None
                        notify(f"已删除会话“{s['title']}”。")
                        st.rerun()

    # 消息列表
    for m in st.session_state["messages"]:
        with st.chat_message("你" if m["role"] == "user" else "AI 助手", avatar=m["role"]):
            st.markdown(m["content"])
            if m.get("trace"):
                with st.expander(f"⚙ 工具调用（{len(m['trace'])} 次）"):
                    for t in m["trace"]:
                        st.code(f"{t['tool']}({t['args']})", language="python")
                        if t.get("result"):
                            st.caption(t["result"])

    # 输入
    st.caption("输入学习问题，按 Enter 发送；Shift+Enter 换行。")
    prompt = st.chat_input("输入学习问题", key="chat_prompt")
    if not prompt:
        return

    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("你", avatar="user"):
        st.markdown(prompt)

    try:
        with st.spinner("AI 正在思考…（最长约 2 分钟）"):
            # 完整历史交给 Agent，最多 50 条（与原接口上限一致）
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state["messages"][-50:]
            ]
            result = run_agent(history)
        with session_scope() as db:
            st.session_state["session_id"] = repo.record_chat_turn(
                db, st.session_state.get("session_id"), prompt, result["reply"]
            )
        st.session_state["messages"].append(
            {"role": "assistant", "content": result["reply"], "trace": result["trace"]}
        )
        notify(f"AI 回复：{result['reply']}", "chat_prompt")
    except (LLMConfigError, LLMCallError) as e:
        st.error(f"AI 调用失败：{e}")
        return  # 出错不 rerun，否则错误信息会立刻被冲掉
    except Exception as e:  # 兜底：数据库/向量库等意外错误
        st.error(f"出错了：{e}")
        return
    st.rerun()


def _load_session(session_id: int) -> None:
    """打开一条历史会话：从库里恢复消息列表。"""
    with session_scope() as db:
        rows = repo.get_chat_messages(db, session_id)
    st.session_state["messages"] = [
        {"role": r["role"], "content": r["content"]} for r in (rows or [])
    ]
    st.session_state["session_id"] = session_id
    notify("已打开历史会话。", "chat_prompt")
    st.rerun()


# ---------- 页面：学习计划 ----------


def page_plans() -> None:
    st.title("📅 学习计划")
    with session_scope() as db:
        plans = repo.list_plans(db)
    if not plans:
        st.info("还没有学习计划。对 AI 说「帮我制定一个 30 天的 Python 学习计划」即可生成。")
        return

    for i, p in enumerate(plans):
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                badges = [f"{p['days']} 天", "最新" if i == 0 else ""]
                badges.append("进行中" if p.get("status") == "active" else "已完成")
                st.subheader(p["topic"])
                st.caption(" ｜ ".join(b for b in badges if b) + f" ｜ 创建于 {p['created_at'][:10]}")
            with c2:
                if confirm_button(f"删除计划：{p['topic']}（计划 #{p['id']}）", f"del_p{p['id']}"):
                    with session_scope() as db:
                        repo.delete_plan(db, p["id"])
                    notify(f"已删除计划“{p['topic']}”。")
                    st.rerun()
            _stage_table(p, show_title=False)


# ---------- 页面：学习任务 ----------


def page_tasks() -> None:
    st.title("✅ 学习任务")

    if st.session_state.pop("_clear_task_title", False):
        st.session_state["task_title"] = ""
    with st.form("new_task"):
        st.subheader("新建任务")
        t1, t2, t3 = st.columns([3, 1.5, 1])
        title = t1.text_input("任务名称", placeholder="例如：完成 AVL 树练习题", key="task_title")
        due = t2.date_input("截止日期（可不选）", value=None)
        submitted = t3.form_submit_button("保存任务", type="primary")
    if submitted and title and title.strip():
        with session_scope() as db:
            repo.create_task(db, title.strip(), due.isoformat() if due else "")
        st.session_state["_clear_task_title"] = True
        notify(f"已保存任务“{title.strip()}”。", "task_title")
        st.rerun()
    elif submitted:
        st.error("请输入任务名称。")
        notify("请输入任务名称。", "task_title")

    filt = st.radio("按任务状态筛选", ["全部", "待完成", "已完成"], horizontal=True, key="task_filter")
    status = {"待完成": "pending", "已完成": "completed"}.get(filt)
    with session_scope() as db:
        tasks = repo.list_tasks(db, status)
    if not tasks:
        st.info("没有任务。")
        return
    for t in tasks:
        task_row(t, "tasks")


# ---------- 页面：知识库 ----------

DOC_STATUS = {
    "processing": ("⏳ 解析中", None),
    "ready": ("✅ 已入库", "green"),
    "failed": ("❌ 失败", "red"),
}


def page_knowledge() -> None:
    st.title("📖 知识库")

    # 清空上一次的上传控件（上传成功后由下一次运行执行）
    if st.session_state.pop("_clear_uploader", False):
        st.session_state.pop("pdf_uploader", None)

    uploaded = st.file_uploader("上传 PDF 学习资料（≤20MB）", type=["pdf"], key="pdf_uploader")
    if uploaded is not None and st.button("上传", type="primary"):
        content = uploaded.getvalue()
        if not content:
            st.error("文件内容为空")
        elif len(content) > MAX_SIZE:
            st.error("文件不能超过 20MB")
        else:
            _save_and_process(uploaded.name or "document.pdf", content)
            st.session_state["_clear_uploader"] = True
            notify(f"资料“{uploaded.name}”已上传，正在解析。", "pdf_uploader")
            st.rerun()

    knowledge_documents()


@st.fragment(run_every=2)
def knowledge_documents() -> None:
    """Refresh document status without blocking keyboard interaction for 180s."""
    with session_scope() as db:
        docs = repo.list_documents(db)

    if docs:
        for d in docs:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([4, 1.5, 1.5, 1])
                with c1:
                    st.markdown(f"**{d['filename']}**")
                with c2:
                    label, color = DOC_STATUS.get(d["status"], (d["status"], None))
                    if d["status"] == "ready" and d["chunk_count"]:
                        label += f"（{d['chunk_count']} 块）"
                    with st.container(key=f"document_status_{d['id']}"):
                        st.write(f"{d['filename']}：{label}")
                        if d.get("error"):
                            st.error(f"解析失败：{d['error']}")
                with c3:
                    st.caption(f"{d['size'] // 1024} KB")
                with c4:
                    delete_label = f"删除资料：{d['filename']}（资料 #{d['id']}）"
                    if d["status"] == "processing":
                        st.button(delete_label, key=f"del_d{d['id']}", disabled=True)
                        st.caption("解析期间暂不可删除，完成后可操作。")
                    elif confirm_button(delete_label, f"del_d{d['id']}"):
                        if _delete_document(d["id"]):
                            notify(f"已删除资料“{d['filename']}”。")
                            st.rerun()
    else:
        st.info("知识库是空的。上传 PDF 后，AI 回答会引用其中内容并标注页码。")

    if any(d["status"] == "processing" for d in docs):
        st.info("正在解析并向量化，状态每 2 秒更新；等待时可以继续操作其他控件或页面。")


def _save_and_process(name: str, content: bytes) -> None:
    """保存 PDF 并起后台线程解析（解析 → 切块 → 向量化 → 标记状态）。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", Path(name).name)
    filepath = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    filepath.write_bytes(content)
    with session_scope() as db:
        doc = repo.create_document(db, filename=safe_name, filepath=str(filepath), size=len(content))
    threading.Thread(
        target=_process_document, args=(doc["id"], str(filepath)), daemon=True
    ).start()


def _process_document(doc_id: int, filepath: str) -> None:
    """后台线程：解析 PDF → 切块 → 向量化入 ChromaDB → 标记 ready。
    任一步失败则标记 failed 并记录原因。"""
    try:
        store = get_vector_store()
        chunks = store.split_to_chunks(pdf_loader(filepath))
        if not chunks:
            raise ValueError("未能从 PDF 提取到文本（可能是扫描版图片 PDF，暂不支持 OCR）")

        with session_scope() as db:
            doc = repo.get_document(db, doc_id, include_chunks=False)
            filename = doc["filename"] if doc else str(filepath)
        store.add_document(doc_id, filename, chunks)

        with session_scope() as db:
            repo.update_document(
                db, doc_id, status="ready", chunk_count=len(chunks), chunks=chunks, error=""
            )
    except Exception as e:  # 后台任务必须兜底，状态落到 failed
        try:
            with session_scope() as db:
                repo.update_document(db, doc_id, status="failed", error=str(e)[:500])
        except Exception:
            pass


def _delete_document(doc_id: int) -> bool:
    """删向量 → 删记录 + 磁盘文件。

    先删向量：若向量删除失败则中止整个删除（DB 记录保留，用户可重试），
    避免"记录已删、向量残留"的孤儿向量被检索到。
    """
    try:
        get_vector_store().delete_document(doc_id)
    except Exception as e:
        st.error(f"删除知识库向量失败，已取消删除：{e}")
        return False
    with session_scope() as db:
        filepath = repo.delete_document(db, doc_id)
    if filepath:
        try:
            Path(filepath).unlink(missing_ok=True)
        except OSError:
            pass  # 文件删除失败不阻塞记录删除
    return True


# ---------- 页面：学习统计 ----------


def page_stats() -> None:
    st.title("📈 学习统计")
    with session_scope() as db:
        stats = repo.get_statistics(db)
        records = repo.list_records(db, 50)

    c1, c2, c3 = st.columns(3)
    c1.metric("学习天数", f"{stats['study_days']} 天")
    c2.metric("完成任务", f"{stats['tasks_completed']} / {stats['tasks_total']}")
    c3.metric("完成率", stats["completion_rate"])

    st.subheader("最近动态")
    events = records[:12]
    if not events:
        st.info("还没有学习动态。")
        return
    KIND_LABEL = {
        "plan_created": "📅 新计划",
        "plan_deleted": "🗑 删计划",
        "task_created": "✅ 新任务",
        "task_completed": "🎉 完成任务",
        "task_deleted": "🗑 删任务",
    }
    for r in events:
        c1, c2, c3 = st.columns([1.2, 4, 1])
        with c1:
            st.caption(r["time"][:16])
        with c2:
            st.markdown(f"{KIND_LABEL.get(r['kind'], '·')} {r['entry']}")
        with c3:
            if confirm_button(f"删除记录：{r['entry']}（记录 #{r['id']}）", f"del_r{r['id']}"):
                with session_scope() as db:
                    repo.delete_record(db, r["id"])
                notify("已删除所选学习记录。")
                st.rerun()


# ---------- 主入口 ----------

PAGES_FN = {
    "仪表盘": page_dashboard,
    "AI 对话": page_chat,
    "学习计划": page_plans,
    "学习任务": page_tasks,
    "知识库": page_knowledge,
    "学习统计": page_stats,
}


def main() -> None:
    with st.sidebar:
        st.header("🎓 StudyMate")
        st.caption("你的 AI 学习搭子")
        page = st.radio("页面导航", PAGES, key="page_navigation")
        st.divider()
        config_warnings()

    previous = st.session_state.get("_a11y_page")
    if previous is not None and previous != page:
        st.session_state["confirm_key"] = None
        notify(f"已打开{page}。", "page_navigation")
    st.session_state["_a11y_page"] = page
    render_accessibility(announce=False)
    PAGES_FN[page]()
    render_accessibility()


main()
