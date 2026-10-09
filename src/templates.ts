export type Template = { id: string; title: string; category: string; description: string; image: string; prompt: string; style: string; color: string };
export const templates: Template[] = [
  {
    id: 'cobalt', title: '蓝调香氛', category: '商业摄影', description: '光影 × 材质 × 纯粹色彩',
    image: '/examples/editorial.png', style: 'editorial', color: 'cobalt',
    prompt: '一瓶通透的钴蓝色玻璃香水，放在薄荷绿的几何展台上，深蓝色摄影棚背景。强烈侧光穿过玻璃，在台面形成清晰的折射光斑。极简构图，奢侈品杂志级商业摄影，保留玻璃细节，无文字、无标志。',
  },
  {
    id: 'planet', title: '远行者', category: '概念艺术', description: '在现实之外，探索另一种可能',
    image: '/examples/cinematic.png', style: 'cinematic', color: 'coral',
    prompt: '一位身穿橙色宇航服的独行者站在广阔的赭红色沙丘上，天空中悬挂着巨大的粉色带环行星。青蓝色黎明天光，细腻风沙纹理，电影级超现实摄影，人物比例很小，强调空间与孤独感，无文字。',
  },
  {
    id: 'pavilion', title: '静谧之间', category: '空间设计', description: '混凝土、自然与安静的对话',
    image: '/examples/architecture.png', style: 'natural', color: 'mint',
    prompt: '极简混凝土建筑展亭，一个巨大的圆形开口框住葱郁的热带树木与平静水面。温暖的米灰色混凝土，明亮的黄绿色日光，丰富的材质与光影，安静的建筑杂志摄影，胶片质感，无文字、无人。',
  },
];
export const styles = [
  { id: 'natural', name: '真实摄影', label: 'PHOTO' },
  { id: 'editorial', name: '商业大片', label: 'EDITORIAL' },
  { id: 'cinematic', name: '电影质感', label: 'CINEMA' },
  { id: '3d', name: '立体渲染', label: '3D' },
  { id: 'illustration', name: '创意插画', label: 'ART' },
  { id: 'anime', name: '动漫风格', label: 'ANIME' },
];
export const styleName = (id: string) => styles.find(style => style.id === id)?.name || id;
