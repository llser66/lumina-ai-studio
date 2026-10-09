import { Aperture, ArrowDown, ArrowUpRight, Eye, Layers3, ShieldCheck, Sparkles, Workflow } from 'lucide-react';
import { templates } from './templates';
import './demo-preview.css';

const flow = [
  { number: '01', title: '描述创意', description: '用文字整理画面主体、场景、光线与风格。' },
  { number: '02', title: '编排工作流', description: '通过 LangGraph 组织提示词处理、检查与任务状态。' },
  { number: '03', title: '连接图像模型', description: '按任务选择已配置的生图服务，并保存创作记录。' },
];

export default function DemoPreview() {
  return <div className="demo-page">
    <header className="demo-header">
      <a className="demo-brand" href="#top" aria-label="Lumina 首页"><span className="demo-brand-mark"><Aperture size={21} /></span><span>lumina<small>映造</small></span></a>
      <nav aria-label="页面导航"><a href="#作品">作品示例</a><a href="#流程">创作流程</a><a href="#项目">项目介绍</a></nav>
      <span className="demo-mode"><Eye size={15} />只读预览</span>
    </header>

    <main className="demo-main" id="top">
      <section className="demo-hero">
        <div className="demo-hero-copy">
          <span className="demo-eyebrow"><Sparkles size={14} />AI IMAGE CREATION WORKSPACE</span>
          <h1>让灵感，<br /><em>接着发生。</em></h1>
          <p>Lumina 是一个面向创作者的 AI 图片创作网站原型，探索从文字描述、模型连接到作品管理的完整体验。</p>
          <a className="demo-cta" href="#作品">浏览作品示例<ArrowDown size={16} /></a>
          <div className="demo-note"><ShieldCheck size={15} />公开展示模式，不会提交内容或调用模型</div>
        </div>
        <figure className="demo-hero-art">
          <img src={templates[0].image} alt={templates[0].title + '：内置灵感示例'} />
          <figcaption><span>01 / BUILT-IN INSPIRATION</span><strong>{templates[0].title}</strong><small>{templates[0].category} · {templates[0].description}</small></figcaption>
        </figure>
      </section>

      <section className="demo-section" id="作品">
        <div className="demo-section-heading"><div><span className="demo-eyebrow">SELECTED INSPIRATION</span><h2>画面灵感</h2></div><p>以下为项目内置的静态示例作品</p></div>
        <div className="demo-art-grid">{templates.map((template, index) => <article className="demo-art-card" key={template.id}>
          <div className="demo-art-image"><img src={template.image} alt={template.title + '：内置灵感示例'} /><span>0{index + 1}</span></div>
          <div className="demo-art-caption"><div><span>{template.category}</span><h3>{template.title}</h3><p>{template.description}</p></div><ArrowUpRight size={18} /></div>
        </article>)}</div>
      </section>

      <section className="demo-section demo-flow-section" id="流程">
        <div className="demo-section-heading"><div><span className="demo-eyebrow">FROM PROMPT TO IMAGE</span><h2>把创作拆成清晰步骤</h2></div><Workflow size={24} /></div>
        <div className="demo-flow-grid">{flow.map(step => <article className="demo-flow-card" key={step.number}><span>{step.number}</span><h3>{step.title}</h3><p>{step.description}</p></article>)}</div>
      </section>

      <section className="demo-project" id="项目">
        <div><span className="demo-eyebrow">PROJECT OVERVIEW</span><h2>为创作流程搭建完整的应用原型</h2><p>项目使用 React 与 TypeScript 构建创作界面，FastAPI 提供服务端接口，LangGraph 编排任务流程，SQLite 保存任务和作品数据。模型连接支持 API Key、订阅授权与自定义接口。</p></div>
        <div className="demo-stack"><span><Layers3 size={15} />React · TypeScript</span><span><Workflow size={15} />FastAPI · LangGraph</span><span><ShieldCheck size={15} />账号隔离 · 凭证加密</span></div>
      </section>

      <footer className="demo-footer"><a className="demo-brand" href="#top"><span className="demo-brand-mark"><Aperture size={18} /></span><span>lumina<small>映造</small></span></a><span>只读作品预览 · 登录、模型配置和真实生图功能已关闭</span></footer>
    </main>
  </div>;
}
