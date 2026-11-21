import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  GitBranch,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Send,
  Square,
  X,
} from 'lucide-react';
import { api } from '../api';
import { useWorkspace } from '../context/WorkspaceContext';
import { useProjectStream } from '../hooks/useProjectStream';
import AutopilotBar from '../components/AutopilotBar';
import BacklogSidebar from '../components/BacklogSidebar';
import RunCard from '../components/RunCard';
import Button from '../components/ui/Button';
import MarkdownView from '../components/ui/MarkdownView';
import StatusBadge from '../components/ui/StatusBadge';
import { useToast } from '../components/ui/ToastProvider';

function upsertById(list, item) {
  if (!item?.id) return list;
  const exists = list.some((x) => x.id === item.id);
  return exists ? list.map((x) => (x.id === item.id ? { ...x, ...item } : x)) : [...list, item];
}

export default function ChatPage() {
  const { projectId: routeProjectId } = useParams();
  const toast = useToast();
  const { activeProjectId, activeProject, setActiveProjectId, setSseStatus, setPendingDeliveries } =
    useWorkspace();
  const projectId = routeProjectId || activeProjectId;

  const [messages, setMessages] = useState([]);
  const [runs, setRuns] = useState([]);
  const [runEventsById, setRunEventsById] = useState({});
  const [tasks, setTasks] = useState([]);
  const [autopilot, setAutopilot] = useState({ status: 'idle', running: false, round: 0, max_rounds: 5 });
  const [loading, setLoading] = useState(true);

  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [autopilotBusy, setAutopilotBusy] = useState(false);
  const [backlogOpen, setBacklogOpen] = useState(true);

  const fileInputRef = useRef(null);
  const streamEndRef = useRef(null);

  useEffect(() => {
    if (routeProjectId && routeProjectId !== activeProjectId) {
      setActiveProjectId(routeProjectId);
    }
  }, [routeProjectId, activeProjectId, setActiveProjectId]);

  const loadWorkspace = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const [msgs, runList, taskList, autoState, deliveries] = await Promise.all([
        api.listMessages(projectId),
        api.listRuns(projectId),
        api.listTasks(projectId),
        api.getAutopilot(projectId).catch(() => ({ status: 'idle', running: false, round: 0, max_rounds: 5 })),
        api.listDeliveries(projectId).catch(() => []),
      ]);
      setMessages(msgs);
      setRuns(runList);
      setTasks(taskList);
      setAutopilot(autoState);
      setPendingDeliveries(deliveries.filter((d) => d.status === 'pending').length);
    } catch (err) {
      toast.error(err.message || '加载对话工作台失败');
    } finally {
      setLoading(false);
    }
  }, [projectId, setPendingDeliveries, toast]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadWorkspace();
    }, 0);
    return () => clearTimeout(id);
  }, [loadWorkspace]);

  const handleStreamEvent = useCallback(
    (eventType, payload) => {
      if (!payload) return;
      if (eventType === 'chat_message') {
        setMessages((prev) => upsertById(prev, payload));
      } else if (eventType === 'chat_delta') {
        const msgId = payload.message_id;
        const delta = String(payload.delta || '');
        if (!msgId || !delta) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === msgId ? { ...m, content: `${m.content || ''}${delta}` } : m)),
        );
      } else if (eventType === 'chat_done') {
        setMessages((prev) => upsertById(prev, payload));
      } else if (eventType === 'run_updated') {
        setRuns((prev) => upsertById(prev, payload));
      } else if (eventType === 'run_event') {
        const rid = payload.run_id;
        if (!rid) return;
        setRunEventsById((prev) => ({
          ...prev,
          [rid]: [...(prev[rid] || []), payload],
        }));
      } else if (eventType === 'autopilot_updated') {
        setAutopilot(payload);
      } else if (eventType === 'task_updated') {
        setTasks((prev) => upsertById(prev, payload));
      } else if (eventType === 'delivery_created') {
        setPendingDeliveries((c) => c + 1);
        toast.info('有新的 Sub-agent 交付可供审阅');
      }
    },
    [setPendingDeliveries, toast],
  );

  const sseStatus = useProjectStream(projectId, handleStreamEvent);
  useEffect(() => {
    setSseStatus(sseStatus);
  }, [sseStatus, setSseStatus]);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages.length, runs.length]);

  const isManagerStreaming = useMemo(
    () => messages.some((m) => m.role === 'manager' && m.meta?.status === 'streaming'),
    [messages],
  );

  const runsByParent = useMemo(() => {
    const grouped = new Map();
    const standalone = [];
    const msgIds = new Set(messages.map((m) => m.id));
    for (const run of runs) {
      if (run.parent_message_id && msgIds.has(run.parent_message_id)) {
        const arr = grouped.get(run.parent_message_id) || [];
        arr.push(run);
        grouped.set(run.parent_message_id, arr);
      } else {
        standalone.push(run);
      }
    }
    return { grouped, standalone };
  }, [messages, runs]);

  const handleUploadFiles = async (event) => {
    const files = [...(event.target.files || [])];
    event.target.value = '';
    if (!files.length || !projectId) return;
    setUploading(true);
    try {
      const uploaded = [];
      for (const file of files) {
        const att = await api.uploadAttachment(projectId, file);
        uploaded.push(att);
      }
      setAttachments((prev) => [...prev, ...uploaded]);
    } catch (err) {
      toast.error(err.message || '附件上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleSend = async (e) => {
    e?.preventDefault?.();
    const content = draft.trim();
    if (!content && attachments.length === 0) return;
    setSending(true);
    try {
      const res = await api.sendMessage(projectId, { content, attachments });
      if (res?.user_message) {
        setMessages((prev) => upsertById(prev, res.user_message));
      }
      if (res?.assistant_message) {
        setMessages((prev) => upsertById(prev, res.assistant_message));
      }
      if (res?.steered) {
        toast.info('已向正在思考的 Manager 插入实时引导');
      }
      setDraft('');
      setAttachments([]);
    } catch (err) {
      toast.error(err.message || '发送消息失败');
    } finally {
      setSending(false);
    }
  };

  const handleStopManager = async () => {
    try {
      await api.stopChat(projectId);
      toast.info('已请求停止当前 Manager 回复');
    } catch (err) {
      toast.error(err.message || '停止回复失败');
    }
  };

  const handleStartAutopilot = async (payload) => {
    setAutopilotBusy(true);
    try {
      const next = await api.startAutopilot(projectId, payload);
      setAutopilot(next);
      toast.success('已启动多轮自主续跑');
    } catch (err) {
      toast.error(err.message || '启动自主续跑失败');
    } finally {
      setAutopilotBusy(false);
    }
  };

  const handleStopAutopilot = async () => {
    setAutopilotBusy(true);
    try {
      const next = await api.stopAutopilot(projectId);
      setAutopilot(next);
      toast.info('已停止多轮自主续跑');
    } catch (err) {
      toast.error(err.message || '停止自主续跑失败');
    } finally {
      setAutopilotBusy(false);
    }
  };

  if (!projectId) {
    return (
      <div className="v2-page">
        <div className="v2-card">
          <h2 className="v2-card__title">请先选择或导入项目</h2>
          <p className="v2-card__subtitle">进入「项目」页面选择本地代码库后即可开始协作对话。</p>
          <div style={{ marginTop: '1rem' }}>
            <Link to="/projects" className="product-button product-button--primary">
              前往项目列表
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="v2-page--full">
      <div className={`v2-chat-shell${backlogOpen ? '' : ' v2-chat-shell--collapsed'}`}>
        <section className="v2-chat-column" aria-label="Manager 协作对话">
          <header className="v2-chat-topbar">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', minWidth: 0 }}>
              <h1 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 680 }}>
                {activeProject?.name || '协作对话'}
              </h1>
              <StatusBadge tone="neutral">
                <GitBranch size={12} style={{ marginRight: '4px' }} />
                {activeProject?.default_branch || 'main'}
              </StatusBadge>
            </div>
            <div className="v2-actions">
              <Button
                variant="secondary"
                className="v2-btn-sm v2-btn-icon"
                onClick={() => setBacklogOpen((v) => !v)}
              >
                {backlogOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                {backlogOpen ? '收起任务栏' : `展开任务栏 (${tasks.length})`}
              </Button>
            </div>
          </header>

          <AutopilotBar
            autopilot={autopilot}
            busy={autopilotBusy}
            onStart={handleStartAutopilot}
            onStop={handleStopAutopilot}
          />

          <div className="v2-chat-stream" role="log" aria-live="polite">
            {loading ? (
              <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--product-text-muted)' }}>
                正在加载对话与运行记录...
              </div>
            ) : messages.length === 0 && runs.length === 0 ? (
              <div className="v2-card" style={{ maxWidth: '42rem', margin: '2rem auto', textAlign: 'center' }}>
                <h2 className="v2-card__title">与项目 Manager 开始协作</h2>
                <p className="v2-card__subtitle">
                  直接描述需求、Bug 或重构想法；Manager 会结合项目记忆拆解任务，并派发 Coder / Researcher / Reviewer / QA 在隔离 worktree 中并行执行。
                </p>
              </div>
            ) : (
              <>
                {messages.map((msg) => {
                  const childRuns = runsByParent.grouped.get(msg.id) || [];
                  const roleLabel =
                    msg.role === 'user' ? '你' : msg.role === 'manager' ? 'Manager' : '系统通知';
                  return (
                    <div key={msg.id} style={{ display: 'grid', gap: '0.6rem' }}>
                      <article className={`v2-msg v2-msg--${msg.role}`}>
                        <div className="v2-msg__header">
                          <span className="v2-msg__role">
                            {roleLabel}
                            {msg.meta?.status === 'streaming' ? (
                              <StatusBadge tone="active">回复中...</StatusBadge>
                            ) : null}
                          </span>
                          <span>{msg.created_at ? new Date(msg.created_at).toLocaleTimeString() : ''}</span>
                        </div>
                        {msg.attachments?.length ? (
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.5rem' }}>
                            {msg.attachments.map((att, idx) => (
                              <span key={att.path || att.name || idx} className="v2-chip">
                                <Paperclip size={11} />
                                {att.name || att.filename || '附件'}
                              </span>
                            ))}
                          </div>
                        ) : null}
                        <MarkdownView content={msg.content || (msg.meta?.status === 'streaming' ? '正在思考...' : '')} />
                      </article>

                      {childRuns.map((run) => (
                        <RunCard
                          key={run.id}
                          run={run}
                          projectId={projectId}
                          liveEvents={runEventsById[run.id]}
                          onStopped={(updated) => updated && setRuns((prev) => upsertById(prev, updated))}
                        />
                      ))}
                    </div>
                  );
                })}

                {runsByParent.standalone.map((run) => (
                  <RunCard
                    key={run.id}
                    run={run}
                    projectId={projectId}
                    liveEvents={runEventsById[run.id]}
                    onStopped={(updated) => updated && setRuns((prev) => upsertById(prev, updated))}
                  />
                ))}
              </>
            )}
            <div ref={streamEndRef} />
          </div>

          <form className="v2-chat-composer" onSubmit={handleSend}>
            <div className="v2-chat-composer__inner">
              {attachments.length > 0 ? (
                <div className="v2-chat-composer__attachments">
                  {attachments.map((att, idx) => (
                    <span key={att.path || att.name || idx} className="v2-chip">
                      <Paperclip size={12} />
                      {att.name || att.filename || '附件'}
                      <button
                        type="button"
                        aria-label="移除附件"
                        onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== idx))}
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}

              <div className="v2-chat-composer__row">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  hidden
                  onChange={handleUploadFiles}
                />
                <Button
                  type="button"
                  variant="secondary"
                  className="v2-btn-icon"
                  busy={uploading}
                  onClick={() => fileInputRef.current?.click()}
                  aria-label="上传附件"
                >
                  <Paperclip size={16} aria-hidden="true" />
                </Button>
                <textarea
                  className="v2-textarea"
                  rows={2}
                  placeholder={
                    isManagerStreaming
                      ? 'Manager 正在响应，输入内容可实时插话引导 (Steer)...'
                      : '输入需求、问题或任务指令（Ctrl/Cmd + Enter 发送）...'
                  }
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                      handleSend(e);
                    }
                  }}
                />
                {isManagerStreaming ? (
                  <Button
                    type="button"
                    variant="danger"
                    className="v2-btn-icon"
                    onClick={handleStopManager}
                  >
                    <Square size={14} aria-hidden="true" />
                    停止
                  </Button>
                ) : null}
                <Button
                  type="submit"
                  variant="primary"
                  className="v2-btn-icon"
                  busy={sending}
                  disabled={!draft.trim() && attachments.length === 0}
                >
                  <Send size={14} aria-hidden="true" />
                  {isManagerStreaming ? '插话引导' : '发送'}
                </Button>
              </div>
            </div>
          </form>
        </section>

        {backlogOpen ? (
          <BacklogSidebar
            projectId={projectId}
            tasks={tasks}
            onTasksChange={setTasks}
            onClose={() => setBacklogOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
