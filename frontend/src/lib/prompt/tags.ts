export type PromptTagCategory = {
  id: 'quality' | 'style' | 'composition' | 'lighting' | 'color' | 'material';
  tags: {
    label: {
      en: string;
      'zh-CN': string;
    };
    value: string;
  }[];
};

export const promptTagCategories: PromptTagCategory[] = [
  {
    id: 'quality',
    tags: [
      { label: { en: 'High detail', 'zh-CN': '高细节' }, value: 'high detail' },
      { label: { en: 'Ultra sharp', 'zh-CN': '超清晰' }, value: 'ultra sharp' },
      { label: { en: 'Clean composition', 'zh-CN': '干净构图' }, value: 'clean composition' },
      { label: { en: 'Fine texture', 'zh-CN': '精细纹理' }, value: 'fine texture' }
    ]
  },
  {
    id: 'style',
    tags: [
      { label: { en: 'Cinematic', 'zh-CN': '电影感' }, value: 'cinematic' },
      { label: { en: 'Watercolor', 'zh-CN': '水彩' }, value: 'watercolor' },
      { label: { en: 'Editorial', 'zh-CN': '杂志编辑风' }, value: 'editorial' },
      { label: { en: '3D render', 'zh-CN': '3D 渲染' }, value: '3D render' }
    ]
  },
  {
    id: 'composition',
    tags: [
      { label: { en: 'Macro shot', 'zh-CN': '微距' }, value: 'macro shot' },
      { label: { en: 'Close-up', 'zh-CN': '近景' }, value: 'close-up' },
      { label: { en: 'Wide angle', 'zh-CN': '广角' }, value: 'wide angle' },
      { label: { en: 'Overhead view', 'zh-CN': '俯拍' }, value: 'overhead view' }
    ]
  },
  {
    id: 'lighting',
    tags: [
      { label: { en: 'Soft rim light', 'zh-CN': '柔和轮廓光' }, value: 'soft rim light' },
      { label: { en: 'Golden hour', 'zh-CN': '黄金时刻' }, value: 'golden hour' },
      { label: { en: 'Volumetric lighting', 'zh-CN': '体积光' }, value: 'volumetric lighting' },
      { label: { en: 'Studio lighting', 'zh-CN': '棚拍光' }, value: 'studio lighting' }
    ]
  },
  {
    id: 'color',
    tags: [
      { label: { en: 'Muted palette', 'zh-CN': '低饱和色盘' }, value: 'muted palette' },
      { label: { en: 'Vibrant colors', 'zh-CN': '鲜明色彩' }, value: 'vibrant colors' },
      { label: { en: 'Warm tone', 'zh-CN': '暖色调' }, value: 'warm tone' },
      { label: { en: 'High contrast', 'zh-CN': '高对比' }, value: 'high contrast' }
    ]
  },
  {
    id: 'material',
    tags: [
      { label: { en: 'Glass texture', 'zh-CN': '玻璃质感' }, value: 'glass texture' },
      { label: { en: 'Brushed metal', 'zh-CN': '拉丝金属' }, value: 'brushed metal' },
      { label: { en: 'Fabric fibers', 'zh-CN': '织物纤维' }, value: 'fabric fibers' },
      { label: { en: 'Ceramic finish', 'zh-CN': '陶瓷表面' }, value: 'ceramic finish' }
    ]
  }
];
