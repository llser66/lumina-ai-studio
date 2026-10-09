import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Aperture, ArrowUpRight, Bell, BookOpen, Check, CheckCircle2, ChevronDown, ChevronRight,
  CircleHelp, Clock3, Download, Ellipsis, Expand, ImagePlus, Images, Layers3, LayoutGrid,
  Loader2, LogOut, Menu, Plus, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles,
  Star, Trash2, WandSparkles, X, Zap,
} from 'lucide-react';
import { api, ApiError, Asset, Health, Job, Quota, setCsrf, Spec, statusText, terminal, User } from './api';
import { styles, styleName, Template, templates } from './templates';
import ModelSettings from './ModelSettings';
import { ModelConfiguration } from './model-types';
import DemoPreview from './DemoPreview';
import './styles.css';
import './model-settings.css';

type View = 'studio' | 'library' | 'favorites' | 'tasks' | 'settings';
const titles: Record<View, string> = { studio: '创作工作台', library: '我的作品', favorites: '收藏夹', tasks: '任务中心', settings: '模型与订阅' };
const defaultSpec: Spec = { prompt: '', negative_prompt: '', style: 'natural', size: '1024x1024', quality: 'medium', enhance: true, reference_id: null };
function initialDraft(): Spec {
  try {
    const value = JSON.parse(localStorage.getItem('lumina-draft') || '{}');
    return { ...defaultSpec, prompt: typeof value.prompt === 'string' ? value.prompt.slice(0, 2000) : '', negative_prompt: typeof value.negative_prompt === 'string' ? value.negative_prompt.slice(0, 500) : '', style: styles.some(s => s.id === value.style) ? value.style : 'natural',
      size: ['1024x1024', '1536x1024', '1024x1536'].includes(value.size) ? value.size : '1024x1024',
      quality: ['low', 'medium', 'high'].includes(value.quality) ? value.quality : 'medium', enhance: typeof value.enhance === 'boolean' ? value.enhance : true };
  } catch { return defaultSpec; }
}
function getView(): View { const name = location.pathname.slice(1); return name in titles ? name as View : 'studio'; }
function pendingSubmission() {
  try {
    const value = JSON.parse(sessionStorage.getItem('lumina-pending') || 'null');
    return value && typeof value.key === 'string' && typeof value.body === 'string' && /^[0-9a-f-]{36}$/.test(value.key) && value.body.length < 16000 ? value as { key: string; body: string } : null;
  } catch { return null; }
}
function dateLabel(value: number) { return new Date(value * 1000).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }

function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => { dialog?.close(); }; }, []);
  return <dialog ref={ref} className={'modal ' + (wide ? 'modal-wide' : '')} aria-label={title}
    onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <button className="icon-button modal-close" aria-label="关闭窗口" onClick={onClose}><X size={20} /></button>
    {children}
  </dialog>;
}

function App() {
  const [view, setView] = useState<View>(getView);
  const [health, setHealth] = useState<Health | null>(null);
  const [modelConfiguration, setModelConfiguration] = useState<ModelConfiguration | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [spec, setSpec] = useState<Spec>(initialDraft);
  const [reference, setReference] = useState<Asset | null>(null);
  const [authMode, setAuthMode] = useState<'login' | 'register' | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'inspiration' | 'recent'>('inspiration');
  const [category, setCategory] = useState('全部');
  const [taskFilter, setTaskFilter] = useState('全部');
  const [query, setQuery] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [accountMenu, setAccountMenu] = useState(false);
  const [archiveId, setArchiveId] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [loading, setLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const authEpoch = useRef(0);
  const activeUser = useRef<User | null>(null);
  const referenceRestored = useRef(false);
  const submitKey = useRef<{ key: string; body: string } | null>(pendingSubmission());
  const fileInput = useRef<HTMLInputElement>(null);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const selectedJob = jobs.find(job => job.id === selectedJobId);
  const activeJobs = jobs.filter(job => !terminal.has(job.status));
  const imageReady = modelConfiguration ? modelConfiguration.image_ready : !!health?.provider_ready;
  const onModelsUpdate = useCallback((configuration: ModelConfiguration) => {
    if (activeUser.current?.id === configuration.owner_id) setModelConfiguration(configuration);
  }, []);
  const loadModels = useCallback(async () => {
    const epoch = authEpoch.current;
    try { const configuration = await api<ModelConfiguration>('/models/config'); if (epoch === authEpoch.current && activeUser.current?.id === configuration.owner_id) setModelConfiguration(configuration); }
    catch (e) { if (epoch === authEpoch.current && !(e instanceof ApiError && e.status === 401)) setError((e as Error).message); }
  }, []);

  const notify = useCallback((message: string) => setToast(message), []);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 4500); return () => clearTimeout(timer); }, [toast]);
  useEffect(() => {
    try { localStorage.setItem('lumina-draft', JSON.stringify({ prompt: spec.prompt, negative_prompt: spec.negative_prompt, style: spec.style, size: spec.size, quality: spec.quality, enhance: spec.enhance })); } catch { /* Storage may be disabled. */ }
  }, [spec]);
  useEffect(() => {
    if (loading) return;
    try {
      if (user && reference) sessionStorage.setItem('lumina-reference', JSON.stringify({ owner: user.id, id: reference.id }));
      else sessionStorage.removeItem('lumina-reference');
    } catch { /* Reference restoration is optional when browser storage is unavailable. */ }
  }, [user, reference, loading]);
  useEffect(() => { const listener = () => setView(getView()); addEventListener('popstate', listener); return () => removeEventListener('popstate', listener); }, []);

  const loadHealth = useCallback(async () => {
    try { setHealth(await api<Health>('/health')); setError(''); } catch (e) { setHealth(null); setError((e as Error).message); }
  }, []);
  const loadJobs = useCallback(async () => {
    const epoch = authEpoch.current;
    try { const result = await api<{ items: Job[]; next_offset: number | null }>('/jobs?limit=60'); if (epoch !== authEpoch.current) return; setJobs(result.items); setNextOffset(result.next_offset); setHistoryError(''); }
    catch (e) {
      if (epoch !== authEpoch.current) return;
      if (e instanceof ApiError && e.status === 401) { activeUser.current = null; setModelConfiguration(null); setUser(null); setJobs([]); setQuota(null); setReference(null); setSpec(s => ({ ...s, reference_id: null })); }
      else setHistoryError((e as Error).message);
    }
  }, []);
  const loadSession = useCallback(async () => {
    const epoch = authEpoch.current;
    try { const result = await api<{ user: User; csrf_token: string; quota: Quota }>('/auth/me'); if (epoch !== authEpoch.current) return false; activeUser.current = result.user; setUser(result.user); setCsrf(result.csrf_token); setQuota(result.quota);
      if (!referenceRestored.current) {
        referenceRestored.current = true;
        try {
          const saved = JSON.parse(sessionStorage.getItem('lumina-reference') || 'null');
          if (saved?.owner === result.user.id && typeof saved.id === 'string' && /^[0-9a-f-]{36}$/.test(saved.id)) {
            setReference({ id: saved.id, image_url: '/api/assets/' + saved.id });
            setSpec(current => ({ ...current, reference_id: saved.id }));
          }
        } catch { /* Corrupt local preferences never affect authorization. */ }
      }
      return true; }
    catch (e) { if (epoch !== authEpoch.current) return false; if (!(e instanceof ApiError) || e.status !== 401) setError((e as Error).message); return false; }
  }, []);
  useEffect(() => {
    let alive = true;
    Promise.all([loadHealth(), loadSession().then(ok => { if (ok && alive) return Promise.all([loadJobs(), loadModels()]); })]).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [loadHealth, loadSession, loadJobs, loadModels]);
  useEffect(() => {
    if (!user) return;
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void loadJobs(); }, activeJobs.length ? 5000 : 20000);
    return () => clearInterval(timer);
  }, [user, activeJobs.length, loadJobs]);
  const activeId = activeJobs[0]?.id;
  useEffect(() => {
    if (!activeId) return;
    const epoch = authEpoch.current;
    const source = new EventSource('/api/jobs/' + activeId + '/events');
    source.addEventListener('job', event => {
      if (epoch !== authEpoch.current) { source.close(); return; }
      try { const job = JSON.parse((event as MessageEvent).data) as Job; setJobs(rows => rows.map(row => row.id === job.id ? job : row));
        if (terminal.has(job.status)) { source.close(); void loadSession(); if (job.status === 'succeeded') notify('新的作品已保存到作品库'); }
      } catch { source.close(); }
    });
    source.onerror = () => source.close(); // Periodic refresh remains available after disconnect.
    return () => source.close();
  }, [activeId, loadSession, notify]);

  function navigate(next: View) { setView(next); history.pushState({}, '', '/' + next); setMobileNav(false); setQuery(''); }
  function change<K extends keyof Spec>(key: K, value: Spec[K]) { setSpec(current => ({ ...current, [key]: value })); setError(''); }
  function useTemplate(template: Template) {
    setSpec(current => ({ ...current, prompt: template.prompt, style: template.style, reference_id: null }));
    setReference(null); setSelectedTemplate(null); navigate('studio'); notify('创作描述已填入，可以继续调整');
    setTimeout(() => promptInput.current?.focus(), 50);
  }
  function reuse(job: Job, edit = false) {
    setSpec({ prompt: edit ? '保持画面主体与构图，将背景修改为' : job.prompt, negative_prompt: job.negative_prompt, style: job.style, size: job.size, quality: job.quality, enhance: job.enhance, reference_id: edit ? job.asset_id : null });
    setReference(edit && job.asset_id && job.image_url ? { id: job.asset_id, image_url: job.image_url } : null);
    setSelectedJobId(null); navigate('studio');
    setTimeout(() => promptInput.current?.focus(), 50);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!user) { setAuthMode('login'); return; }
    if (!imageReady) { setError('生图服务尚未连接。请在模型与订阅页面连接服务并保存默认模型。'); return; }
    if (quota?.remaining === 0) { setError('今天的创作额度已用完，请等待额度恢复。'); return; }
    setBusy(true); setError('');
    const epoch = authEpoch.current;
    const body = JSON.stringify(spec);
    if (!submitKey.current || submitKey.current.body !== body) submitKey.current = { key: crypto.randomUUID(), body };
    try { sessionStorage.setItem('lumina-pending', JSON.stringify(submitKey.current)); } catch { /* Server idempotency still applies within this page. */ }
    try {
      const job = await api<Job>('/jobs', { method: 'POST', headers: { 'Idempotency-Key': submitKey.current.key }, body });
      if (epoch !== authEpoch.current) return;
      setJobs(rows => [job, ...rows.filter(row => row.id !== job.id)]); setTab('recent');
      submitKey.current = null;
      try { sessionStorage.removeItem('lumina-pending'); } catch { /* Storage may be disabled. */ }
      void loadSession(); notify('创作已加入队列，可以继续探索灵感');
    } catch (e) { if (epoch === authEpoch.current) setError((e as Error).message); } finally { if (epoch === authEpoch.current) setBusy(false); }
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    if (!user) { setAuthMode('login'); return; }
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 8 * 1024 * 1024) { notify('请选择不超过 8 MB 的 PNG、JPG 或 WebP 图片'); return; }
    setUploading(true);
    const epoch = authEpoch.current;
    try { const asset = await api<Asset>('/assets', { method: 'POST', headers: { 'Content-Type': file.type }, body: file }); if (epoch !== authEpoch.current) return; setReference(asset); change('reference_id', asset.id); }
    catch (e) { if (epoch === authEpoch.current) notify((e as Error).message); } finally { if (epoch === authEpoch.current) setUploading(false); if (fileInput.current) fileInput.current.value = ''; }
  }
  async function authenticate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setAuthBusy(true); setAuthError('');
    const epoch = ++authEpoch.current;
    const form = new FormData(event.currentTarget);
    const body = { email: String(form.get('email')), password: String(form.get('password')), ...(authMode === 'register' ? { name: String(form.get('name')) } : {}) };
    try {
      const result = await api<{ user: User; csrf_token: string }>('/auth/' + authMode, { method: 'POST', body: JSON.stringify(body) });
      if (epoch !== authEpoch.current) return;
      activeUser.current = result.user; setUser(result.user); setCsrf(result.csrf_token); setAuthMode(null); await Promise.all([loadSession(), loadJobs(), loadModels()]); notify('欢迎回来，' + result.user.name);
    } catch (e) { setAuthError((e as Error).message); } finally { setAuthBusy(false); }
  }
  async function logout() {
    ++authEpoch.current;
    activeUser.current = null;
    try { await api('/auth/logout', { method: 'POST' }); setModelConfiguration(null); setUser(null); setCsrf(''); setQuota(null); setJobs([]); setReference(null); setSpec(defaultSpec); setBusy(false); setUploading(false); submitKey.current = null; try { sessionStorage.removeItem('lumina-pending'); } catch { /* Storage may be disabled. */ } setAccountMenu(false); navigate('studio'); }
    catch (e) { notify((e as Error).message); }
  }
  async function favorite(job: Job) {
    try { const result = await api<Job>('/jobs/' + job.id, { method: 'PATCH', body: JSON.stringify({ favorite: !job.favorite }) }); setJobs(rows => rows.map(row => row.id === result.id ? result : row)); }
    catch (e) { notify((e as Error).message); }
  }
  async function cancel(job: Job) {
    try { await api('/jobs/' + job.id + '/cancel', { method: 'POST' }); await Promise.all([loadJobs(), loadSession()]); notify('任务已取消，额度已释放'); }
    catch (e) { notify((e as Error).message); }
  }
  async function archive() {
    if (!archiveId) return;
    try { await api('/jobs/' + archiveId, { method: 'DELETE' }); setJobs(rows => rows.filter(row => row.id !== archiveId)); setSelectedJobId(null); setArchiveId(null); notify('已移出作品库'); }
    catch (e) { notify((e as Error).message); }
  }
  async function loadMore() {
    if (nextOffset === null) return;
    try {
      const result = await api<{ items: Job[]; next_offset: number | null }>('/jobs?limit=60&offset=' + nextOffset);
      setJobs(rows => [...rows, ...result.items.filter(item => !rows.some(row => row.id === item.id))]); setNextOffset(result.next_offset);
    } catch (e) { notify((e as Error).message); }
  }
  const visibleJobs = jobs.filter(job => (view !== 'favorites' || job.favorite) && job.prompt.toLowerCase().includes(query.toLowerCase()));
  const visibleTemplates = templates.filter(template => category === '全部' || template.category === category);
  const completed = jobs.filter(job => job.status === 'succeeded');
  const jobCard = (job: Job) => <article className="art-card" key={job.id}>
    <button className="art-image" onClick={() => setSelectedJobId(job.id)} aria-label={'查看作品：' + job.prompt}>
      {job.image_url ? <img src={job.image_url} alt={job.prompt} loading="lazy" /> :
        <div className={'job-placeholder ' + job.status}>{terminal.has(job.status) ? <CircleHelp size={32} /> : <Loader2 size={32} className="spin" />}<span>{statusText[job.status]}</span><small>{job.events.at(-1)?.message}</small></div>}
      <span className={'image-badge state-' + job.status}>{statusText[job.status]}</span>
      <span className="image-expand"><Expand size={16} /></span>
    </button>
    <div className="art-caption"><div><h3>{job.prompt}</h3><p>{styleName(job.style)}<span>·</span>{dateLabel(job.created_at)}</p></div>
      <button className={'icon-button ' + (job.favorite ? 'is-favorite' : '')} onClick={() => favorite(job)} aria-label={job.favorite ? '取消收藏' : '收藏作品'} aria-pressed={job.favorite}><Star size={17} fill={job.favorite ? 'currentColor' : 'none'} /></button></div>
  </article>;

  return <div className="app-shell">
    {mobileNav && <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={'sidebar ' + (mobileNav ? 'is-open' : '')}>
      <a className="brand" href="/studio" onClick={e => { e.preventDefault(); navigate('studio'); }}><div className="brand-mark"><Aperture size={24} strokeWidth={2.4} /></div><span>lumina<span className="brand-cn">映造</span></span></a>
      <div className="workspace-label"><span className="workspace-icon"><Layers3 size={15} /></span>个人创作空间<ChevronDown size={14} /></div>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="主导航">
        {([{ id: 'studio', icon: WandSparkles, label: '创作工作台' }, { id: 'library', icon: Images, label: '我的作品' }, { id: 'favorites', icon: Star, label: '收藏夹' }, { id: 'tasks', icon: Clock3, label: '任务中心' }] as const).map(item =>
          <button key={item.id} onClick={() => navigate(item.id)} className={'nav-item ' + (view === item.id ? 'active' : '')} aria-current={view === item.id ? 'page' : undefined}>
            <item.icon size={18} /><span>{item.label}</span>{item.id === 'tasks' && activeJobs.length > 0 && <span className="count-badge">{activeJobs.length}</span>}
          </button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="quota-card"><div className="quota-heading"><Zap size={16} /><span>每日创作额度</span></div>
          {quota ? <><div className="quota-numbers"><b>{quota.remaining}</b><span>/ {quota.limit} 次剩余</span></div><progress value={quota.remaining} max={quota.limit} aria-label="剩余创作额度" /><p>{new Date(quota.resets_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 恢复额度</p></> : <><p>登录后，保存每一次灵感。</p><button className="quiet-button" onClick={() => setAuthMode('register')}>创建你的空间<Plus size={14} /></button></>}
        </div>
        <button className={'nav-item ' + (view === 'settings' ? 'active' : '')} onClick={() => navigate('settings')}><Settings2 size={18} />模型与订阅</button>
        <div className="sidebar-footer"><span>MADE FOR IMAGINATION</span><span className="tiny-spark">✳</span></div>
      </div>
    </aside>
    <div className="main-shell">
      <header className="topbar">
        <div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNav(true)}><Menu size={20} /></button><span>创作空间</span><ChevronRight size={14} /><b>{titles[view]}</b></div>
        <div className="topbar-actions"><button className={'connection-button ' + (imageReady ? 'connected' : '')} onClick={() => navigate('settings')}><span className="connection-dot" />{health ? imageReady ? '生图服务已配置' : '生图服务待连接' : '正在连接服务'}</button>
          <div className="account-container"><button className={user ? 'avatar-button' : 'login-button'} onClick={() => { if (user) setAccountMenu(!accountMenu); else { setAuthError(''); setAuthMode('login'); } }} aria-label={user ? '账号菜单' : '登录账号'}>{user ? user.name.slice(0, 1).toUpperCase() : '登录'}{user && <ChevronDown size={13} />}</button>
            {accountMenu && user && <div className="account-menu"><strong>{user.name}</strong><p>{user.email}</p><button onClick={logout}><LogOut size={16} />退出登录</button></div>}</div>
        </div>
      </header>
      <main>
        <div className="page-heading"><div><div className="eyebrow">{view === 'studio' ? 'YOUR NEXT CREATION STARTS HERE' : 'YOUR CREATIVE SPACE'}</div><h1>{titles[view]}<span className="heading-period">.</span></h1><p>{view === 'studio' ? '把脑海里的画面，变成看得见的灵感。' : view === 'library' ? '每一次想象，都值得被好好保存。' : view === 'favorites' ? '留住让你心动的画面。' : view === 'tasks' ? '每一步进展，都在这里。' : '照顾你的创作空间。'}</p></div>
          {view !== 'studio' && <button className="secondary-button" onClick={() => { navigate('studio'); promptInput.current?.focus(); }}><Plus size={16} />新建创作</button>}
          {view === 'studio' && <span className="studio-tag"><Sparkles size={14} />IMAGE STUDIO</span>}
        </div>
        {historyError && <div className="notice error-notice" role="alert">{historyError}<button onClick={loadJobs}>重新加载</button></div>}
        {view === 'studio' ? <div className="studio-layout">
          <section className="creation-panel" aria-label="创建图片">
            <div className="panel-title"><span><WandSparkles size={18} />新的创作</span><span className="panel-number">01</span></div>
            <form onSubmit={submit}><div className="form-fields">
              <div className="field-heading"><label htmlFor="prompt">画面描述</label><button type="button" className="text-button" onClick={() => useTemplate(templates[Math.floor(Math.random() * templates.length)])}><Sparkles size={13} />来点灵感</button></div>
              <div className="prompt-wrap"><textarea ref={promptInput} id="prompt" value={spec.prompt} onChange={e => change('prompt', e.target.value)} placeholder="一座漂浮在云端的花园，日落时分，光穿过透明的玻璃穹顶…" maxLength={2000} minLength={3} required rows={6} /><div className="prompt-footer"><span>描述主体、场景、光线与氛围</span><span>{spec.prompt.length}/2000</span></div></div>
              <label className="enhance-toggle"><span className="enhance-icon"><WandSparkles size={15} /></span><span>智能优化描述<small>让构图和细节更明确</small></span><input type="checkbox" checked={spec.enhance} onChange={e => change('enhance', e.target.checked)} /><span className="switch" aria-hidden="true" /></label>
              <div className="field-heading"><span>参考图片<span className="optional">可选</span></span>{reference && <button className="text-button" type="button" onClick={() => { setReference(null); change('reference_id', null); }}>移除</button>}</div>
              <input type="file" ref={fileInput} className="file-input" accept="image/png,image/jpeg,image/webp" onChange={e => void upload(e.target.files?.[0])} aria-label="选择参考图片" />
              <button type="button" className={'upload-zone ' + (reference ? 'has-reference' : '')} onClick={() => fileInput.current?.click()} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void upload(e.dataTransfer.files[0]); }} disabled={uploading}>
                {reference ? <><img src={reference.image_url} alt="当前参考图片" /><span>参考图片已添加<small>点击替换图片</small></span><Check size={18} /></> : <>{uploading ? <Loader2 className="spin" size={20} /> : <ImagePlus size={22} />}<span>{uploading ? '正在上传…' : '点击上传或拖入图片'}<small>PNG / JPG / WebP · 最大 8 MB</small></span></>}
              </button>
              <fieldset className="style-fieldset"><legend>视觉风格</legend><div className="style-grid">{styles.map((style, index) => <button type="button" key={style.id} className={'style-option style-' + style.id + (spec.style === style.id ? ' selected' : '')} onClick={() => change('style', style.id)} aria-pressed={spec.style === style.id}><span className="style-symbol">{[<Aperture size={21} />, <Layers3 size={21} />, <Expand size={21} />, <LayoutGrid size={21} />, <Sparkles size={21} />, <Star size={21} />][index]}</span><span>{style.name}</span>{spec.style === style.id && <Check size={11} className="style-check" />}</button>)}</div></fieldset>
              <fieldset className="ratio-fieldset"><legend>画面比例</legend><div className="ratio-options">{[{ value: '1024x1024', label: '1:1', shape: 'square' }, { value: '1536x1024', label: '3:2', shape: 'landscape' }, { value: '1024x1536', label: '2:3', shape: 'portrait' }].map(ratio => <button type="button" key={ratio.value} onClick={() => change('size', ratio.value)} aria-pressed={spec.size === ratio.value} className={'ratio-option ' + (spec.size === ratio.value ? 'selected' : '')}><span className={'ratio-shape ' + ratio.shape} />{ratio.label}</button>)}</div></fieldset>
              <div className="quality-field"><label htmlFor="quality">输出画质</label>{modelConfiguration?.image_controls.quality === false ? <span className="muted">由模型控制</span> : <select id="quality" value={spec.quality} onChange={e => change('quality', e.target.value)}><option value="low">快速 · Low</option><option value="medium">标准 · Medium</option><option value="high">精细 · High</option></select>}</div>
              <button className="advanced-button" type="button" onClick={() => setShowAdvanced(!showAdvanced)} aria-expanded={showAdvanced}><SlidersHorizontal size={14} />排除内容<ChevronDown size={14} /></button>
              {showAdvanced && <textarea className="negative-input" aria-label="不希望出现的内容" placeholder="例如：文字、水印、模糊背景" maxLength={500} value={spec.negative_prompt} onChange={e => change('negative_prompt', e.target.value)} />}
              </div><div className="form-footer">{error && <div className="form-error" role="alert">{error}</div>}
              <button className="generate-button" type="submit" disabled={busy || uploading}>{busy ? <Loader2 className="spin" size={18} /> : <Sparkles size={18} />}{busy ? '正在提交…' : reference ? '生成编辑作品' : '开始生成'}<span>1 张</span></button>
              <p className="generation-note"><ShieldCheck size={12} />作品仅对你可见 · 画质影响费用与用时</p></div>
            </form>
          </section>
          <section className="gallery-panel" aria-label="灵感与作品">
            <div className="gallery-top"><div className="gallery-tabs"><button className={tab === 'inspiration' ? 'selected' : ''} onClick={() => setTab('inspiration')}>灵感精选</button><button className={tab === 'recent' ? 'selected' : ''} onClick={() => setTab('recent')}>最近创作{completed.length > 0 && <span>{completed.length}</span>}</button></div><LayoutGrid size={17} className="muted" /></div>
            {tab === 'inspiration' ? <>
              <div className="gallery-intro"><div><h2>下一张作品，从这里开始</h2><p>选一个方向，让想象继续生长。</p></div><span className="curated-label">CURATED INSPIRATION<small>示例作品 · 可复用描述</small></span></div>
              <div className="filter-row">{['全部', '商业摄影', '概念艺术', '空间设计'].map(label => <button key={label} className={category === label ? 'selected' : ''} onClick={() => setCategory(label)}>{label}</button>)}</div>
              <div className="art-grid template-grid">{visibleTemplates.map(template => <article className={'art-card template-card ' + template.color} key={template.id}>
                <button className="art-image" onClick={() => setSelectedTemplate(template)} aria-label={'查看灵感：' + template.title}><img src={template.image} alt={template.title} /><span className="image-badge">灵感示例</span><span className="image-expand"><Expand size={16} /></span><span className="image-use">探索这个画面<ArrowUpRight size={17} /></span></button>
                <div className="art-caption"><div><h3>{template.title}</h3><p>{template.category}</p></div><button className="icon-button" aria-label={'使用描述：' + template.title} onClick={() => useTemplate(template)}><Plus size={18} /></button></div>
              </article>)}</div>
              <div className="prompt-tip"><div className="tip-icon"><BookOpen size={20} /></div><div><span className="tip-label">A LITTLE CREATIVE TIP</span><h3>好的画面，始于具体的描述。</h3><p>试试「主体 + 场景 + 光线 + 风格」，比“好看的图片”更接近你的想象。</p></div></div>
              <div className="gallery-foot"><span><Aperture size={14} />给灵感一点空间</span><span>CREATE SOMETHING ONLY YOU CAN IMAGINE</span></div>
            </> : <>
              <div className="gallery-intro"><div><h2>你的创作足迹</h2><p>新作品会自动保存在这里。</p></div>{activeJobs.length > 0 && <span className="running-label"><Loader2 className="spin" size={14} />{activeJobs.length} 个任务进行中</span>}</div>
              {jobs.length ? <div className="art-grid">{jobs.slice(0, 12).map(jobCard)}</div> : <EmptyState icon="images" title={user ? '你的第一张作品，即将诞生' : '为你的灵感留一个位置'} message={user ? '写下画面描述，开始创作。' : '登录后，可以查看并保存自己的创作。'} action={!user ? () => setAuthMode('login') : () => promptInput.current?.focus()} actionLabel={user ? '写下描述' : '登录你的空间'} />}
              {jobs.length > 12 && <button className="secondary-button gallery-more" onClick={() => navigate('library')}>查看全部作品</button>}
            </>}
          </section>
        </div> : view === 'library' || view === 'favorites' ? <section className="library-panel">
          <div className="library-toolbar"><span>{view === 'favorites' ? '收藏的画面' : '全部创作'}<b>{visibleJobs.length}</b></span><label className="search-box"><Search size={16} /><input aria-label="搜索作品" placeholder="搜索画面描述…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
          {loading ? <div className="empty-state"><Loader2 className="spin" /></div> : visibleJobs.length ? <div className="art-grid library-grid">{visibleJobs.map(jobCard)}</div> : <EmptyState icon="images" title={query ? '没有找到这个画面' : view === 'favorites' ? '收藏你想再次遇见的灵感' : '这里将收藏你的每一次想象'} message={query ? '换个关键词试试。' : user ? '开始创作，让这里慢慢丰富起来。' : '登录后，查看只属于你的作品。'} action={() => user ? navigate('studio') : setAuthMode('login')} actionLabel={user ? '开始创作' : '登录账号'} />}
          {nextOffset !== null && <button className="secondary-button gallery-more" onClick={loadMore}>加载更多作品</button>}
        </section> : view === 'tasks' ? <section className="tasks-panel">
          <div className="filter-row">{['全部', '进行中', '已完成', '需处理'].map(filter => <button className={taskFilter === filter ? 'selected' : ''} key={filter} onClick={() => setTaskFilter(filter)}>{filter}</button>)}</div>
          {jobs.filter(job => taskFilter === '全部' || (taskFilter === '进行中' ? !terminal.has(job.status) : taskFilter === '已完成' ? job.status === 'succeeded' : ['failed', 'uncertain'].includes(job.status))).map(job => <div className="task-row" key={job.id}>
            <button className="task-thumbnail" onClick={() => setSelectedJobId(job.id)} aria-label="查看任务详情">{job.image_url ? <img src={job.image_url} alt="" /> : terminal.has(job.status) ? <Images size={21} /> : <Loader2 size={21} className="spin" />}</button>
            <button className="task-description" onClick={() => setSelectedJobId(job.id)}><strong>{job.prompt}</strong><span>{dateLabel(job.created_at)} · {styleName(job.style)} · {job.size}</span></button>
            <span className={'task-status state-' + job.status}>{statusText[job.status]}</span>
            {job.status === 'queued' ? <button className="text-button" onClick={() => cancel(job)}>取消</button> : <button className="icon-button" aria-label="查看任务详情" onClick={() => setSelectedJobId(job.id)}><ChevronRight size={17} /></button>}
          </div>)}
          {!jobs.length && <EmptyState icon="clock" title="还没有创作任务" message="开始一次创作，就能在这里看到每一步进展。" action={() => navigate('studio')} actionLabel="去创作" />}
          {jobs.length > 0 && !jobs.some(job => taskFilter === '全部' || (taskFilter === '进行中' ? !terminal.has(job.status) : taskFilter === '已完成' ? job.status === 'succeeded' : ['failed', 'uncertain'].includes(job.status))) && <EmptyState icon="clock" title="这个分类里还没有任务" message="切换分类，查看其他创作。" action={() => setTaskFilter('全部')} actionLabel="查看全部" />}
          {nextOffset !== null && <button className="secondary-button gallery-more" onClick={loadMore}>加载更多任务</button>}
        </section> : <><ModelSettings initial={modelConfiguration} loggedIn={!!user} onLogin={() => setAuthMode('login')} onUpdate={onModelsUpdate} notify={notify} /><details className="workspace-help"><summary>工作台信息与使用说明</summary><section className="settings-grid">
          <div className="settings-card"><div className="settings-card-icon"><Zap size={22} /></div><h2>生图服务</h2><p>{imageReady ? '已配置，可以开始新的创作。' : '请在上方连接生图服务，并保存默认模型。'}</p><dl><div><dt>当前模型</dt><dd>{modelConfiguration?.active_image_model || health?.model || '等待服务响应'}</dd></div><div><dt>连接状态</dt><dd>{imageReady ? '已配置' : '待配置'}</dd></div><div><dt>原图格式</dt><dd>PNG</dd></div></dl><button className="secondary-button" onClick={() => { void loadHealth(); void loadModels(); }}>重新检查连接</button><p className="setting-footnote">配置状态不代表模型权限已验证；实际可用性以供应商结果为准。</p></div>
          <div className="settings-card"><div className="settings-card-icon"><ShieldCheck size={22} /></div><h2>我的空间</h2><p>图片与创作记录仅对当前账号开放。</p><dl><div><dt>账号</dt><dd>{user?.email || '尚未登录'}</dd></div><div><dt>今日剩余额度</dt><dd>{quota ? quota.remaining + ' / ' + quota.limit : '登录后查看'}</dd></div><div><dt>异常任务</dt><dd>结果不明时暂停自动重试</dd></div></dl><button className="secondary-button" onClick={() => user ? navigate('tasks') : setAuthMode('login')}>{user ? '查看我的任务' : '登录账号'}</button></div>
          <div className="settings-card full-width"><h2>使用说明</h2><div className="help-steps"><div><b>01</b><span>描述画面<p>越具体，越接近你想要的结果。</p></span></div><div><b>02</b><span>选择风格与比例<p>上传参考图，可以基于原图编辑。</p></span></div><div><b>03</b><span>保存你的作品<p>生成完成后，下载原图或继续修改。</p></span></div></div></div>
        </section></details></>}
      </main>
    </div>
    {toast && <div className="toast" role="status"><CheckCircle2 size={17} />{toast}<button aria-label="关闭提示" onClick={() => setToast('')}><X size={14} /></button></div>}
    {authMode && <Modal title={authMode === 'login' ? '登录 Lumina' : '创建 Lumina 账号'} onClose={() => setAuthMode(null)}>
      <div className="auth-modal"><div className="auth-brand"><Aperture size={28} /></div><div className="eyebrow">WELCOME TO LUMINA</div><h2>{authMode === 'login' ? '让灵感，接着发生。' : '为想象，留一个空间。'}</h2><p>{authMode === 'login' ? '登录你的个人创作空间。' : '保存作品，追踪进度，继续每一个想法。'}</p>
        <form onSubmit={authenticate}>{authMode === 'register' && <label>你的名字<input name="name" required minLength={2} maxLength={32} autoComplete="nickname" placeholder="怎么称呼你？" /></label>}<label>邮箱<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" autoFocus /></label><label>密码<input name="password" type="password" minLength={8} maxLength={128} autoComplete={authMode === 'login' ? 'current-password' : 'new-password'} required placeholder="至少 8 个字符" /></label>
          {authError && <div className="form-error" role="alert">{authError}</div>}<button className="generate-button" disabled={authBusy}>{authBusy && <Loader2 size={17} className="spin" />}{authMode === 'login' ? '登录' : '创建账号'}</button></form>
        <div className="auth-switch">{authMode === 'login' ? '还没有账号？' : '已经有账号？'}<button onClick={() => { setAuthMode(authMode === 'login' ? 'register' : 'login'); setAuthError(''); }}>{authMode === 'login' ? '创建账号' : '去登录'}</button></div>
      </div>
    </Modal>}
    {selectedTemplate && <Modal title={selectedTemplate.title + ' · 灵感示例'} wide onClose={() => setSelectedTemplate(null)}>
      <div className="detail-layout"><div className="detail-image"><img src={selectedTemplate.image} alt={selectedTemplate.title} /><span className="image-badge">灵感示例</span></div><div className="detail-content"><div className="eyebrow">PROMPT INSPIRATION</div><h2>{selectedTemplate.title}</h2><p className="detail-description">{selectedTemplate.description}</p><span className="detail-chip">{selectedTemplate.category}</span><h3>创作描述</h3><p className="detail-prompt">{selectedTemplate.prompt}</p><p className="setting-footnote">这是一张预置的灵感示例。使用描述后，将通过已连接的模型生成新的作品。</p><button className="generate-button" onClick={() => useTemplate(selectedTemplate)}><Plus size={17} />使用这个描述</button></div></div>
    </Modal>}
    {selectedJob && <Modal title="创作详情" wide onClose={() => setSelectedJobId(null)}>
      <div className="detail-layout"><div className="detail-image">{selectedJob.image_url ? <img src={selectedJob.image_url} alt={selectedJob.prompt} /> : <div className="job-placeholder">{terminal.has(selectedJob.status) ? <CircleHelp size={44} /> : <Loader2 size={44} className="spin" />}<h3>{statusText[selectedJob.status]}</h3><p>{selectedJob.error_message || selectedJob.events.at(-1)?.message}</p></div>}</div>
        <div className="detail-content"><div className="eyebrow">CREATION DETAILS</div><h2>创作详情</h2><span className={'detail-chip state-' + selectedJob.status}>{statusText[selectedJob.status]}</span><h3>原始描述</h3><p className="detail-prompt">{selectedJob.prompt}</p>{selectedJob.enhanced_prompt && <details><summary>查看优化后的描述</summary><p className="detail-prompt">{selectedJob.enhanced_prompt}</p></details>}
          <div className="detail-parameters"><span>{styleName(selectedJob.style)}</span><span>{selectedJob.size}</span><span>{selectedJob.quality}</span></div>
          <h3>创作进展</h3><ol className="event-timeline">{selectedJob.events.map(event => <li key={event.id}><span className="event-dot" /><div>{event.message}<small>{new Date(event.created_at * 1000).toLocaleTimeString('zh-CN')}</small></div></li>)}</ol>
          {selectedJob.status === 'uncertain' && <div className="form-error">请管理员通过任务编号核对供应商结果。为避免重复计费，系统保留该次额度并停止自动重试。<small>任务编号：{selectedJob.id}</small></div>}
          <div className="detail-actions">{selectedJob.image_url && <a className="generate-button" href={selectedJob.image_url + '?download=true'}><Download size={16} />下载原图</a>}{selectedJob.asset_id && <button className="secondary-button" onClick={() => reuse(selectedJob, true)}><WandSparkles size={16} />基于此图编辑</button>}<button className="secondary-button" onClick={() => reuse(selectedJob)}><Plus size={16} />复用描述</button>{selectedJob.status === 'queued' && <button className="secondary-button" onClick={() => cancel(selectedJob)}>取消任务</button>}{terminal.has(selectedJob.status) && <button className="text-button danger-text" onClick={() => setArchiveId(selectedJob.id)}><Trash2 size={14} />移出作品库</button>}</div>
        </div></div>
    </Modal>}
    {archiveId && <Modal title="移出作品库" onClose={() => setArchiveId(null)}><div className="confirm-modal"><h2>将这次创作移出作品库？</h2><p>该记录将不再展示。移出记录不会返还已经使用的创作额度。</p><div><button className="secondary-button" onClick={() => setArchiveId(null)}>保留作品</button><button className="danger-button" onClick={archive}>移出作品库</button></div></div></Modal>}
  </div>;
}

function EmptyState({ title, message, action, actionLabel, icon }: { title: string; message: string; action: () => void; actionLabel: string; icon: 'images' | 'clock' }) {
  return <div className="empty-state"><div className="empty-icon">{icon === 'images' ? <Images size={32} /> : <Clock3 size={32} />}</div><h2>{title}</h2><p>{message}</p><button className="secondary-button" onClick={action}>{actionLabel}</button></div>;
}

const root = createRoot(document.getElementById('root')!);
root.render(import.meta.env.VITE_DEMO_MODE === 'true' ? <DemoPreview /> : <App />);
