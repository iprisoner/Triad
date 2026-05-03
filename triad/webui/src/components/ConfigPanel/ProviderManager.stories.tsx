import type { Meta, StoryObj } from '@storybook/react';
import ProviderManager from './ProviderManager';
import type { Provider } from '@/hooks/useProviders';

// ─── Mock Data ───
const mockProviders: Provider[] = [
  {
    id: 'grok',
    name: 'Grok',
    base_url: 'https://api.x.ai/v1/chat/completions',
    api_key: 'sk-****xxxx',
    context_window: 8192,
    tags: ['creative', 'uncensored'],
    enabled: true,
    description: 'xAI 推出的 Grok 模型，擅长创意写作和开放式对话。',
  },
  {
    id: 'deepseek-chat',
    name: 'DeepSeek Chat',
    base_url: 'https://api.deepseek.com/v1/chat/completions',
    api_key: 'sk-****xxxx',
    context_window: 65536,
    tags: ['reasoning', 'code'],
    enabled: false,
    description: 'DeepSeek 深度推理模型，擅长数学推导和代码生成。',
  },
  {
    id: 'qwen-local',
    name: 'Qwen 14B Local',
    base_url: 'http://0.0.0.0:18000/v1/chat/completions',
    api_key: 'local-no-key',
    context_window: 4096,
    tags: ['chinese', 'code'],
    enabled: true,
    description: '本地部署的 Qwen-14B，支持中文对话和代码补全。',
  },
  {
    id: 'claude-sonnet',
    name: 'Claude 3.5 Sonnet',
    base_url: 'https://api.anthropic.com/v1/messages',
    api_key: 'sk-****xxxx',
    context_window: 200000,
    tags: ['reasoning', 'longform', 'code'],
    enabled: true,
    description: 'Anthropic Claude 3.5 Sonnet，超长上下文窗口，适合长篇小说写作。',
  },
  {
    id: 'gemini-pro',
    name: 'Gemini Pro',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro',
    api_key: 'sk-****xxxx',
    context_window: 32768,
    tags: ['creative', 'chinese'],
    enabled: true,
    description: 'Google Gemini Pro，多模态支持，创意写作和中文能力优秀。',
  },
  {
    id: 'custom-mistral',
    name: 'Mistral 7B 自建',
    base_url: 'http://192.168.1.100:8000/v1/chat/completions',
    api_key: 'sk-local',
    context_window: 8192,
    tags: ['reasoning', 'code', 'creative', 'chinese'],
    enabled: false,
    description: '自建 Mistral 7B 实例，支持多种能力标签。',
  },
];

// ─── Meta ───
const meta: Meta<typeof ProviderManager> = {
  title: 'ConfigPanel/ProviderManager',
  component: ProviderManager,
  parameters: {
    layout: 'padded',
    docs: {
      description: {
        component:
          '模型配置中心（Provider Manager）—— Triad 动态模型注册中心。支持无限添加模型厂商、编辑配置、标签过滤、启用/停用开关、一键测试连接。',
      },
    },
  },
  tags: ['autodocs'],
};

export default meta;

type Story = StoryObj<typeof ProviderManager>;

// ─── Default: 6 Providers with mixed states ───
export const Default: Story = {
  name: '默认视图',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    docs: {
      description: {
        story: '展示 6 个模型卡片（含 2 个停用），默认左侧列表 + 右侧空状态。',
      },
    },
  },
};

// ─── Add Mode ───
export const AddMode: Story = {
  name: '添加模型模式',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    docs: {
      description: {
        story: '点击「添加模型」后进入添加表单模式，展示所有表单字段。',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const addBtn = canvasElement.querySelector('button') as HTMLButtonElement;
    if (addBtn) addBtn.click();
  },
};

// ─── Edit Mode ───
export const EditMode: Story = {
  name: '编辑模型模式',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    docs: {
      description: {
        story: '点击第一个模型卡片（Grok）进入编辑模式，展示右侧编辑表单。',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const cards = canvasElement.querySelectorAll('[role="switch"]');
    // Click on the first card's container (not the switch itself)
    const firstCard = canvasElement.querySelector('.group.cursor-pointer') as HTMLElement;
    if (firstCard) firstCard.click();
  },
};

// ─── Filtered by Tag ───
export const FilteredByReasoning: Story = {
  name: '标签筛选 - 推理',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    docs: {
      description: {
        story: '点击「推理」过滤标签，只展示带有 reasoning 标签的模型。',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const reasoningBtn = Array.from(canvasElement.querySelectorAll('button')).find(
      (b) => b.textContent === '推理'
    ) as HTMLButtonElement;
    if (reasoningBtn) reasoningBtn.click();
  },
};

// ─── Empty State ───
export const EmptyState: Story = {
  name: '空状态',
  args: {
    initialProviders: [],
  },
  parameters: {
    docs: {
      description: {
        story: '没有任何模型时的空状态，提示用户添加第一个模型。',
      },
    },
  },
};

// ─── Mobile View ───
export const MobileView: Story = {
  name: '移动端视图',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    viewport: {
      defaultViewport: 'mobile1',
    },
    docs: {
      description: {
        story: '在移动端视口下，布局变为单列，模型列表和表单切换显示。',
      },
    },
  },
};

// ─── Tablet View ───
export const TabletView: Story = {
  name: '平板视图',
  args: {
    initialProviders: mockProviders,
  },
  parameters: {
    viewport: {
      defaultViewport: 'tablet',
    },
    docs: {
      description: {
        story: '在平板视口下，左侧列表宽度增加，右侧表单区域保持可用。',
      },
    },
  },
};
