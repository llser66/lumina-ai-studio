import { useCallback, useEffect, useRef, useState } from 'react';
import './model-dialog.css';
import { Check, CheckCircle2, ExternalLink, Image, KeyRound, Link2, Loader2, Plus, RotateCcw, ShieldCheck, Sparkles, Unplug, X } from 'lucide-react';
import { api } from './api';
import { ModelConfiguration, ModelConnection, ModelPreferences, OAuthFlow, ProviderInfo } from './model-types';

const labels: Record<string, string> = {
  openai: 'OpenAI / ChatGPT', 'openai-codex': 'OpenAI Codex', openrouter: 'OpenRouter',
  anthropic: 'Anthropic / Claude', google: 'Google Gemini', 'github-copilot': 'GitHub Copilot',
  deepseek: 'DeepSeek', qwen: '通义千问 · 百炼', cloudflare: 'Cloudflare Workers AI', custom: '自定义接口',
};
const monograms: Record<string, string> = { openai: 'O', 'openai-codex': 'CX', openrouter: 'R', anthropic: 'A', google: 'G', 'github-copilot': 'GH', deepseek: 'DS', qwen: 'Q', cloudflare: 'CF', custom: '</>' };
const descriptions: Record<string, string> = {
  openai: 'API 生图，或连接 ChatGPT 账号使用支持的模型。',
  'openai-codex': '通过 Codex 授权连接 ChatGPT 订阅，选择支持图片输入的模型。',
  openrouter: '一个连接，选择支持的生图、文本与视觉模型。生图需要有效 Key 和余额；当前没有免费生图模型。',
  anthropic: '连接 Claude 模型，用于创作描述与视觉理解。',
  google: '连接 Gemini 文本与视觉模型，整理你的视觉想法。',
  'github-copilot': '使用已有 Copilot 订阅连接支持的模型。',
  deepseek: '使用 DeepSeek API Key，选择官方提供的文本与视觉模型。',
  qwen: '使用阿里云百炼 API Key，连接通义千问文本与视觉模型。',
  cloudflare: '使用 Workers AI 免费日额度运行 FLUX.1 schnell 文生图；需要 Account ID 和 API Token。',
  custom: '填写 OpenAI 兼容接口地址和模型 ID，连接自己的模型服务。',
};
const statusLabels: Record<string, string> = { configured: '已保存 · 待验证', verified: '上次验证通过', needs_attention: '需要检查', authorized: '已授权' };
const stageLabels: Record<string, string> = {
  starting: '正在准备授权', waiting_user: '等待本次授权的登录与回调', awaiting_authorization: '请在供应商页面完成登录', awaiting_callback: '等待本次授权的回调',
  awaiting_input: '等待补充授权信息', exchanging_token: '正在交换授权凭证，请稍候', token_exchange: '正在交换授权凭证，请稍候',
  saving: '正在保存连接', connected: '授权凭证已保存', failed: '授权未完成', cancelled: '本次授权已取消',
};
type ConnectionMethod = 'oauth' | 'api_key' | 'custom';
const methods: { id: ConnectionMethod; label: string }[] = [{ id: 'oauth', label: '订阅登录' }, { id: 'api_key', label: 'API Key' }, { id: 'custom', label: '自定义接口' }];

function providerLabel(provider: ProviderInfo) { return labels[provider.id] || provider.name; }
function safeExternalUrl(value?: string) {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined; } catch { return undefined; }
}
function preferredChatModel(provider: ProviderInfo) {
  return provider.chat_models?.find(model => model.input.includes('image'))?.id || provider.chat_models?.[0]?.id || '';
}

function ModelDialog({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); if (previousFocus?.isConnected) previousFocus.focus(); };
  }, []);
  return <dialog ref={ref} className="model-dialog" aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.currentTarget === event.target) onClose(); }}>{children}</dialog>;
}

export default function ModelSettings({ initial, loggedIn, onLogin, onUpdate, notify }: {
  initial: ModelConfiguration | null; loggedIn: boolean; onLogin: () => void;
  onUpdate: (configuration: ModelConfiguration) => void; notify: (message: string) => void;
}) {
  const [configuration, setConfiguration] = useState(initial);
  const [preferences, setPreferences] = useState<ModelPreferences>(initial?.preferences || { image_connection_id: null, image_model: '', text_connection_id: null, text_model: '' });
  const [connecting, setConnecting] = useState<ProviderInfo | null>(null);
  const [method, setMethod] = useState<ConnectionMethod>('api_key');
  const [connectionName, setConnectionName] = useState('');
  const [key, setKey] = useState('');
  const [preferredModel, setPreferredModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [accountId, setAccountId] = useState('');
  const [customModelId, setCustomModelId] = useState('');
  const [customModelName, setCustomModelName] = useState('');
  const [supportsImage, setSupportsImage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [flow, setFlow] = useState<OAuthFlow | null>(null);
  const [answer, setAnswer] = useState('');
  const [removing, setRemoving] = useState<ModelConnection | null>(null);
  const activeFlow = useRef<string | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const refresh = useCallback(async () => {
    if (!loggedIn) return;
    try {
      const current = await api<ModelConfiguration>('/models/config');
      setConfiguration(current); setPreferences(current.preferences); onUpdate(current);
    } catch (e) { setError((e as Error).message); }
  }, [loggedIn, onUpdate]);
  useEffect(() => { if (initial) { setConfiguration(initial); setPreferences(initial.preferences); } else if (loggedIn) void refresh(); }, [initial, loggedIn, refresh]);
  useEffect(() => {
    if (!flow || flow.status !== 'pending') return;
    const id = flow.id;
    let live = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const current = await api<OAuthFlow>('/models/oauth/' + id);
        if (!live || activeFlow.current !== id) return;
        setFlow(current);
        if (current.status === 'connected') {
          setKey(''); setAnswer(''); setError('');
          await refresh(); notify('订阅账号已连接，可以保存所选模型');
        }
      } catch (e) { if (live && activeFlow.current === id) setError((e as Error).message); }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(poll, 1200);
    return () => { live = false; clearInterval(timer); };
  }, [flow?.id, flow?.status, refresh, notify]);

  if (!loggedIn) return <section className="models-signin"><ShieldCheck size={32} /><h2>管理你的模型与订阅</h2><p>登录后，可连接已有模型账号或保存自己的 API Key。</p><button className="generate-button" onClick={onLogin}>登录后配置模型</button></section>;
  const imageConnection = configuration?.connections.find(connection => connection.id === preferences.image_connection_id);
  const textConnection = configuration?.connections.find(connection => connection.id === preferences.text_connection_id);
  const providers = configuration?.providers || [];
  const availableProviders = providers.filter(provider => method === 'custom' ? provider.id === 'custom' : method === 'oauth' ? provider.oauth : provider.api_key && provider.id !== 'custom');
  const visionModels = connecting?.chat_models?.filter(model => model.input.includes('image')) || [];
  const dialogModels = visionModels.length ? visionModels : connecting?.chat_models || [];
  const pending = flow?.status === 'pending';
  const exchanging = flow?.stage === 'exchanging_token' || flow?.stage === 'token_exchange' || flow?.stage === 'saving';
  const authUrl = pending && !exchanging ? safeExternalUrl(flow.auth_url) : undefined;
  const verificationUrl = pending && !exchanging ? safeExternalUrl(flow.verification_url) : undefined;
  const prompt = pending && !exchanging ? flow.prompt : undefined;
  const showManualInput = pending && !exchanging && (prompt?.type === 'manual_code' || Boolean(authUrl && (flow.manual_callback || connecting?.id === 'openai' || connecting?.id === 'openai-codex')));
  const staleAuthorization = error.includes('已有授权正在进行') || error.includes('ChatGPT 授权通道正在使用');

  function resetProvider(provider: ProviderInfo, nextMethod: ConnectionMethod) {
    setConnecting(provider); setMethod(nextMethod); setConnectionName('我的 ' + (nextMethod === 'oauth' && provider.id === 'openai' ? 'ChatGPT 订阅' : providerLabel(provider)));
    setPreferredModel(preferredChatModel(provider)); setKey(''); setAccountId(''); setError(''); setFlow(null); setAnswer(''); activeFlow.current = null;
  }
  function open(provider: ProviderInfo) {
    resetProvider(provider, provider.id === 'custom' ? 'custom' : provider.id === 'github-copilot' || (!provider.api_key && provider.oauth) ? 'oauth' : 'api_key');
    setBaseUrl(''); setAccountId(''); setCustomModelId(''); setCustomModelName(''); setSupportsImage(false);
  }
  function changeMethod(nextMethod: ConnectionMethod) {
    if (busy || pending) return;
    const nextProviders = providers.filter(provider => nextMethod === 'custom' ? provider.id === 'custom' : nextMethod === 'oauth' ? provider.oauth : provider.api_key && provider.id !== 'custom');
    const provider = nextProviders.find(provider => provider.id === connecting?.id) || (nextMethod === 'oauth' ? nextProviders.find(provider => provider.id === 'openai-codex') : undefined) || nextProviders[0];
    if (provider) resetProvider(provider, nextMethod);
  }
  function tabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? methods.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : methods.length - 1)) % methods.length;
    changeMethod(methods[next].id); tabRefs.current[next]?.focus();
  }
  async function startOAuth() {
    if (!connecting) return;
    activeFlow.current = null; setFlow(null); setAnswer(''); setBusy(true); setError('');
    try {
      const current = await api<OAuthFlow>('/models/oauth', { method: 'POST', body: JSON.stringify({ provider: connecting.id, name: connectionName, ...(preferredModel ? { preferred_model: preferredModel } : {}) }) });
      activeFlow.current = current.id; setFlow(current);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function resetOAuth() {
    if (!connecting || busy) return;
    setBusy(true); setError('');
    try {
      await api<{ cancelled: number }>('/models/oauth/reset', { method: 'POST' });
      await startOAuth();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function connect(event: React.FormEvent) {
    event.preventDefault(); if (!connecting) return;
    if (method === 'oauth') return startOAuth();
    setBusy(true); setError('');
    try {
      const body = method === 'custom'
        ? { provider: 'custom', name: connectionName, api_key: key, base_url: baseUrl, model_id: customModelId, model_name: customModelName || customModelId, supports_image: supportsImage, preferred_model: customModelId }
        : { provider: connecting.id, name: connectionName, api_key: key, ...(connecting.id === 'qwen' && baseUrl ? { base_url: baseUrl } : {}), ...(connecting.id === 'cloudflare' ? { account_id: accountId.trim() } : {}), ...(preferredModel ? { preferred_model: preferredModel } : {}) };
      await api('/models/connections', { method: 'POST', body: JSON.stringify(body) });
      setKey(''); setConnecting(null); await refresh(); notify(preferredModel || method === 'custom' ? '连接与所选模型已保存，可验证凭证' : '连接已保存，可以验证凭证并选择默认模型');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function cancelFlow() {
    if (busy) return;
    const previousFlow = flow;
    if (previousFlow?.status === 'pending') {
      setBusy(true); setError('');
      try { await api('/models/oauth/' + previousFlow.id + '/cancel', { method: 'POST' }); }
      catch (e) { setError((e as Error).message); setBusy(false); return; }
    }
    activeFlow.current = null; setConnecting(null); setKey(''); setFlow(null); setAnswer(''); setError(''); setBusy(false);
  }
  async function sendAnswer(event: React.FormEvent) {
    event.preventDefault(); if (!flow?.prompt || flow.status !== 'pending' || exchanging || !answer.trim()) return;
    setBusy(true); setError('');
    try {
      await api('/models/oauth/' + flow.id + '/answer', { method: 'POST', body: JSON.stringify({ prompt_id: flow.prompt.id, answer: answer.trim() }) });
      setAnswer('');
      const current = await api<OAuthFlow>('/models/oauth/' + flow.id);
      if (activeFlow.current === flow.id) {
        setFlow(current);
        if (current.status === 'connected') { await refresh(); notify('订阅账号已连接，可以保存所选模型'); }
      }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function saveDialogModel() {
    if (!flow?.connection_id || !preferredModel) return;
    setBusy(true); setError('');
    try {
      const current = await api<ModelConfiguration>('/models/preferences', { method: 'PUT', body: JSON.stringify({ ...preferences, text_connection_id: flow.connection_id, text_model: preferredModel }) });
      setConfiguration(current); setPreferences(current.preferences); onUpdate(current); setConnecting(null); setFlow(null); activeFlow.current = null;
      notify('所选模型已保存，用于提示词、内容检查与视觉理解');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function savePreferences() {
    setBusy(true); setError('');
    try { const current = await api<ModelConfiguration>('/models/preferences', { method: 'PUT', body: JSON.stringify(preferences) }); setConfiguration(current); onUpdate(current); notify('默认模型已保存，新创作将使用这些模型'); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function check(connection: ModelConnection) {
    setBusy(true);
    try { const result = await api<{ message: string }>('/models/connections/' + connection.id + '/check', { method: 'POST' }); await refresh(); notify(result.message); }
    catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return; setBusy(true);
    try { await api('/models/connections/' + removing.id, { method: 'DELETE' }); setRemoving(null); await refresh(); notify('模型连接已断开'); }
    catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  }
  function chooseImage(id: string) {
    const connection = configuration?.connections.find(connection => connection.id === id);
    const preferred = connection?.image_models.find(model => model.id === 'gpt-image-2.5-sunburst' || model.id === 'google/gemini-2.5-flash-image') || connection?.image_models[0];
    setPreferences(current => ({ ...current, image_connection_id: id || null, image_model: preferred?.id || '' }));
  }
  function chooseText(id: string) {
    const connection = configuration?.connections.find(connection => connection.id === id);
    const preferred = connection?.chat_models.find(model => model.input.includes('image')) || connection?.chat_models[0];
    setPreferences(current => ({ ...current, text_connection_id: id || null, text_model: preferred?.id || '' }));
  }

  return <section className="model-settings">
    <div className="model-overview"><div className="model-overview-icon"><Link2 size={21} /></div><div><h2>把你常用的模型，连接到创作空间</h2><p>连接已有订阅、API Key 或自定义接口，分别设置视觉理解与生图模型。</p></div><span className="sdk-badge">pi-ai {configuration?.sdk_version || '1.0.4'}</span></div>
    {error && !connecting && <div className="form-error" role="alert">{error}</div>}
    <div className="provider-grid">{providers.map(provider => <article className={'provider-card provider-' + provider.id} key={provider.id}>
      <div className="provider-card-heading"><span className="provider-monogram">{monograms[provider.id] || provider.name.charAt(0)}</span><div><h3>{providerLabel(provider)}</h3><span>{provider.id === 'custom' ? 'OpenAI 兼容接口' : <>{provider.api_key ? 'API Key' : ''}{provider.api_key && provider.oauth ? ' · ' : ''}{provider.oauth ? provider.subscription ? '订阅授权' : '账号授权' : ''}</>}</span></div></div>
      <p>{descriptions[provider.id] || '连接你的模型服务，选择支持的模型。'}</p><button className="secondary-button" onClick={() => open(provider)}><Plus size={15} />添加连接</button>
    </article>)}</div>

    <div className="model-section-title"><h2>已保存的连接<span>{configuration?.connections.length || 0}</span></h2><span>凭证只保存在服务端</span></div>
    {configuration?.connections.length ? <div className="connection-list">{configuration.connections.map(connection => <article className="connection-row" key={connection.id}>
      <div className="connection-row-icon">{connection.auth_method === 'oauth' ? <Link2 size={18} /> : <KeyRound size={18} />}</div><div className="connection-row-details"><h3>{connection.name}{connection.subscription && <span className="connection-kind">订阅</span>}</h3><p>{labels[connection.provider] || connection.provider} · {connection.hint}</p><div className="capability-tags">{connection.image_models.length > 0 && <span><Image size={12} />生图{connection.image_models.some(model => model.input.includes('image')) ? ' / 编辑' : ''}</span>}{connection.chat_models.length > 0 && <span><Sparkles size={12} />文本处理</span>}{connection.chat_models.some(model => model.input.includes('image')) && <span><Image size={12} />视觉理解</span>}</div></div>
      <div className="connection-row-actions"><span className={'credential-status status-' + connection.status}>{statusLabels[connection.status] || '已保存'}</span><button className="text-button" onClick={() => check(connection)} disabled={busy}>验证凭证</button><button className="icon-button" onClick={() => setRemoving(connection)} aria-label={'断开连接：' + connection.name}><Unplug size={17} /></button></div>
    </article>)}</div> : <div className="model-empty"><KeyRound size={24} /><span>还没有连接。选择上方供应商，添加 API Key 或授权订阅账号。</span></div>}

    <div className="model-section-title"><h2>默认创作模型</h2><span>新任务使用保存后的模型</span></div>
    <div className="model-routing-grid">
      <div className="model-route-card"><div className="model-route-heading"><Image size={18} /><h3>生图与图片编辑</h3></div><label htmlFor="image-connection">模型连接</label><select id="image-connection" value={preferences.image_connection_id || ''} onChange={e => chooseImage(e.target.value)}><option value="">{configuration?.server_default_available ? '使用服务器默认生图服务' : '请选择生图连接'}</option>{configuration?.connections.filter(connection => connection.image_models.length > 0).map(connection => <option key={connection.id} value={connection.id}>{connection.name}</option>)}</select>
        <label htmlFor="image-model">生图模型</label><select id="image-model" value={preferences.image_model} onChange={e => setPreferences(current => ({ ...current, image_model: e.target.value }))} disabled={!imageConnection}><option value="">{imageConnection ? '选择模型' : '跟随服务器默认配置'}</option>{imageConnection?.image_models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}</select>
        <p>只列出支持生成图片的连接。图片输入用于视觉理解，生图需要另外选择支持图片输出的服务。</p>
      </div>
      <div className="model-route-card"><div className="model-route-heading"><Sparkles size={18} /><h3>提示词、内容检查与视觉理解</h3></div><label htmlFor="text-connection">模型连接</label><select id="text-connection" value={preferences.text_connection_id || ''} onChange={e => chooseText(e.target.value)}><option value="">{imageConnection && !imageConnection.chat_models.length ? '请选择独立文本模型' : '跟随生图连接的默认文本模型'}</option>{configuration?.connections.filter(connection => connection.chat_models.length > 0).map(connection => <option key={connection.id} value={connection.id}>{connection.name}</option>)}</select>
        <label htmlFor="text-model">文本模型</label><select id="text-model" value={preferences.text_model} onChange={e => setPreferences(current => ({ ...current, text_model: e.target.value }))} disabled={!textConnection}><option value="">{textConnection ? '选择模型' : '自动选择'}</option>{textConnection?.chat_models.map(model => <option key={model.id} value={model.id}>{model.name}{model.input.includes('image') ? ' · 视觉' : ''} · {model.id}</option>)}</select>
        <p>支持图片输入的模型标记为「视觉」。API 与订阅实际可用模型由供应商权限决定。</p>
      </div>
    </div>
    <div className="model-save-row"><p><ShieldCheck size={14} />连接验证不会生成图片。模型调用可能按供应商规则计费。</p><button className="generate-button" disabled={busy} onClick={savePreferences}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />}保存默认模型</button></div>

    {connecting && <ModelDialog title="连接模型账号" onClose={() => void cancelFlow()}>
      <button className="icon-button modal-close" onClick={() => void cancelFlow()} aria-label="关闭模型连接窗口"><X size={20} /></button>
      <div className="auth-brand"><Link2 size={25} /></div><div className="eyebrow">MODEL SETTINGS</div><h2>模型配置</h2><p className="model-dialog-intro">连接自己的模型，让 AI 理解你的创作。</p>
      <div className="connection-method-tabs" role="tablist" aria-label="模型连接方式">{methods.map((item, index) => <button key={item.id} id={'model-tab-' + item.id} ref={element => { tabRefs.current[index] = element; }} type="button" role="tab" aria-selected={method === item.id} aria-controls="model-connection-panel" tabIndex={method === item.id ? 0 : -1} className={method === item.id ? 'selected' : ''} disabled={busy || pending || !providers.some(provider => item.id === 'custom' ? provider.id === 'custom' : item.id === 'oauth' ? provider.oauth : provider.api_key && provider.id !== 'custom')} onClick={() => changeMethod(item.id)} onKeyDown={event => tabKeyDown(event, index)}>{item.label}</button>)}</div>
      <div id="model-connection-panel" role="tabpanel" aria-labelledby={'model-tab-' + method}>
        <label htmlFor="dialog-provider">模型服务商</label><select id="dialog-provider" value={connecting.id} disabled={busy || pending || flow?.status === 'connected'} onChange={e => { const provider = providers.find(provider => provider.id === e.target.value); if (provider) resetProvider(provider, method); }}>{availableProviders.map(provider => <option key={provider.id} value={provider.id}>{providerLabel(provider)}</option>)}</select>
        {method !== 'custom' && connecting.id !== 'cloudflare' && <div className="dialog-model-choice"><div className="dialog-label-row"><label htmlFor="dialog-model">{visionModels.length ? '视觉模型' : '文本模型'}</label><span>{visionModels.length ? '仅显示支持图片输入的模型' : '该服务当前提供文本模型'}</span></div><select id="dialog-model" value={preferredModel} disabled={busy || pending || !dialogModels.length} onChange={e => setPreferredModel(e.target.value)}>{!dialogModels.length && <option value="">连接后加载可用模型</option>}{dialogModels.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}</select><p className="setting-footnote">所选模型用于提示词、内容检查与视觉理解；生图服务在页面下方单独设置。</p></div>}
        {!flow ? <form onSubmit={connect}>
          <label>连接名称<input value={connectionName} onChange={e => setConnectionName(e.target.value)} minLength={2} maxLength={60} required disabled={busy} /></label>
          {method !== 'oauth' ? <>
            <label>API Key<input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} minLength={8} maxLength={8192} required disabled={busy} placeholder="只发送到服务端，不会回显完整密钥" /></label>
            {method === 'custom' && <><label>接口地址<input type="url" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" pattern="https://.*" maxLength={2048} required disabled={busy} /></label><div className="custom-model-fields"><label>模型 ID<input value={customModelId} onChange={e => setCustomModelId(e.target.value)} placeholder="供应商提供的模型 ID" maxLength={200} required disabled={busy} /></label><label>模型名称<input value={customModelName} onChange={e => setCustomModelName(e.target.value)} placeholder="便于识别的名称" maxLength={120} disabled={busy} /></label></div><label className="custom-vision-toggle"><input type="checkbox" checked={supportsImage} onChange={e => setSupportsImage(e.target.checked)} disabled={busy} />支持图片输入（视觉理解）</label><p className="setting-footnote">填写 OpenAI 兼容服务的 HTTPS 地址，例如以 /v1 结尾。勾选后，该模型会标记为支持图片输入。</p></>}
            {method === 'api_key' && connecting.id === 'cloudflare' && <><label htmlFor="cloudflare-account-id">Cloudflare Account ID<input id="cloudflare-account-id" value={accountId} onChange={event => setAccountId(event.target.value)} pattern="[0-9a-fA-F]{32}" minLength={32} maxLength={32} required disabled={busy} placeholder="32 位账号 ID" /></label><p className="setting-footnote"><a href="https://developers.cloudflare.com/workers-ai/get-started/rest-api/" target="_blank" rel="noopener noreferrer">查看官方获取 Account ID 与 API Token 指南</a>。请使用有 Workers AI 运行权限的 Token。验证按钮只检查 Token 是否有效；实际生图权限与每日免费额度由首次生成确认。FLUX.1 schnell 仅支持文字生图，不支持参考图编辑。</p></>}
            {method === 'api_key' && connecting.id !== 'cloudflare' && <p className="setting-footnote">{connecting.id === 'qwen' ? '使用阿里云百炼（DashScope）API Key，请确认所选模型已开通。' : connecting.id === 'deepseek' ? '使用 DeepSeek 开放平台 API Key，模型调用按供应商规则计费。' : 'API 请求由服务端发送到供应商官方地址，保存后可验证凭证。'}</p>}
            {method === 'api_key' && connecting.id === 'qwen' && <><label htmlFor="qwen-base-url">百炼 API Base URL（可选）<input id="qwen-base-url" type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" maxLength={2048} disabled={busy} /></label><p className="setting-footnote">新版百炼控制台可在工作空间查看 Base URL。API Key 与工作空间区域须匹配；留空时使用兼容的北京默认地址。</p></>}
          </> : <div className="subscription-note"><ShieldCheck size={21} /><div><strong>使用你的订阅账号连接</strong><p>登录流程由模型供应商提供。授权凭证只保存在服务端，不会显示在页面中。</p></div></div>}
          {error && <div className="form-error" role="alert">{error}</div>}{staleAuthorization && <button type="button" className="secondary-button" disabled={busy} onClick={() => void resetOAuth()}>清理旧授权并重新开始</button>}<button className="generate-button" disabled={busy}>{busy ? <Loader2 className="spin" size={16} /> : <Link2 size={16} />}{method === 'oauth' ? '开始账号授权' : '保存连接'}</button>
        </form> : <div className="oauth-flow">
          <div className={'oauth-state oauth-' + flow.status} role={flow.status === 'failed' ? 'alert' : 'status'}>{pending ? <Loader2 className="spin" size={23} /> : flow.status === 'connected' ? <CheckCircle2 size={23} /> : <Unplug size={23} />}<div><span>{flow.message}</span>{flow.stage && <small>{stageLabels[flow.stage] || '授权进度已更新'}</small>}{flow.http_status && <small>供应商响应 HTTP {flow.http_status}</small>}</div></div>
          {pending && <div className="oauth-session-note"><ShieldCheck size={14} /><span>只使用本次授权链接；旧链接的授权信息无法用于新的连接。</span></div>}
          {authUrl && <a className="secondary-button oauth-open-link" href={authUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} />打开供应商授权页面</a>}
          {verificationUrl && <><div className="device-code"><span>在授权页面输入这组代码</span><strong>{flow.user_code}</strong></div><a className="secondary-button oauth-open-link" href={verificationUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} />打开账号授权页面</a></>}
          {(prompt || showManualInput) && <form onSubmit={sendAnswer} className={showManualInput ? 'oauth-manual-form' : ''}>
            {showManualInput ? <><label htmlFor="oauth-answer">授权码或回调地址</label><p className="setting-footnote">在浏览器完成登录后，如果连接没有自动更新，将最终回调地址或授权码粘贴到这里。请使用本次授权产生的内容。</p><input id="oauth-answer" type="password" autoComplete="off" value={answer} onChange={e => setAnswer(e.target.value)} required maxLength={8192} disabled={busy} placeholder={prompt?.placeholder || '粘贴授权码或回调地址'} />{!prompt && <p className="setting-footnote" role="status">等待授权接收准备完成，请保留回调信息。</p>}</> : prompt && <label>{prompt.message}{prompt.type === 'select' ? <select value={answer} onChange={e => setAnswer(e.target.value)} required disabled={busy}><option value="">请选择</option>{prompt.options?.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select> : <input type={prompt.type === 'secret' ? 'password' : 'text'} autoComplete="off" value={answer} onChange={e => setAnswer(e.target.value)} required maxLength={8192} disabled={busy} placeholder={prompt.placeholder || (connecting.id === 'github-copilot' ? 'github.com' : '')} />}</label>}
            <button className="secondary-button" disabled={busy || !prompt || !answer.trim()}>{busy && <Loader2 className="spin" size={15} />}提交授权信息</button>
          </form>}
          {error && <div className="form-error" role="alert">{error}</div>}
          {flow.status === 'connected' && <div className="oauth-complete-actions">{preferredModel && flow.connection_id && <button className="generate-button" disabled={busy} onClick={() => void saveDialogModel()}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />}保存所选模型</button>}<button className="secondary-button" onClick={() => { setConnecting(null); setFlow(null); activeFlow.current = null; }}>完成，选择默认模型</button></div>}
          {flow.status !== 'pending' && flow.status !== 'connected' && <><p className="setting-footnote">本次授权链接已失效。重新授权会创建新的登录链接。</p><button className="generate-button" disabled={busy} onClick={() => void startOAuth()}><RotateCcw size={16} />重新授权</button></>}
          {pending && <button className="text-button" onClick={() => void cancelFlow()}>取消授权</button>}
        </div>}
      </div>
    </ModelDialog>}
    {removing && <ModelDialog title="断开模型连接" onClose={() => setRemoving(null)}><h2>断开「{removing.name}」？</h2><p className="oauth-explanation">服务端将移除该连接的凭证。已选用此连接的新任务需要重新配置模型；已经发出的供应商请求可能仍在处理。</p><div className="confirm-actions"><button className="secondary-button" onClick={() => setRemoving(null)}>保留连接</button><button className="danger-button" onClick={remove} disabled={busy}>确认断开</button></div></ModelDialog>}
  </section>;
}
