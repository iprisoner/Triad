import type { Meta, StoryObj } from '@storybook/react';
import SkillMarketTab from './SkillMarketTab';

const meta: Meta<typeof SkillMarketTab> = {
  title: 'ConfigPanel/SkillMarketTab',
  component: SkillMarketTab,
  parameters: {
    layout: 'padded',
    docs: {
      description: {
        component:
          '技能与插件市场（Skill & Plugin Market）—— Triad 三层 AI Agent 融合系统的插件与技能管理面板。支持 MCP Tools 和 OpenClaw Skills 的浏览、搜索、分类筛选和一键启用/禁用。',
      },
    },
  },
  tags: ['autodocs'],
};

export default meta;

type Story = StoryObj<typeof SkillMarketTab>;

// ─── Default View ───
export const Default: Story = {
  name: '默认视图',
  parameters: {
    docs: {
      description: {
        story: '默认打开 MCP 硬件外设 Tab，展示全部 8 个插件卡片。',
      },
    },
  },
};

// ─── Skills Tab ───
export const SkillsTab: Story = {
  name: '技能 Tab',
  parameters: {
    docs: {
      description: {
        story: '切换到「大脑认知技能」Tab，展示 6 个技能卡片（含 3 个自我进化技能的金色徽章）。',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const trigger = canvasElement.querySelector('[data-state="inactive"][value="skills"]') as HTMLElement;
    if (trigger) trigger.click();
  },
};

// ─── Narrow Mobile View ───
export const MobileView: Story = {
  name: '移动端视图',
  parameters: {
    viewport: {
      defaultViewport: 'mobile1',
    },
    docs: {
      description: {
        story: '在移动端视口下，卡片网格变为单列，分类标签支持横向滚动。',
      },
    },
  },
};

// ─── Tablet View ───
export const TabletView: Story = {
  name: '平板视图',
  parameters: {
    viewport: {
      defaultViewport: 'tablet',
    },
    docs: {
      description: {
        story: '在平板视口下，卡片网格变为两列。',
      },
    },
  },
};

// ─── Empty Search Result ───
export const EmptySearch: Story = {
  name: '搜索无结果',
  parameters: {
    docs: {
      description: {
        story: '输入不存在的搜索词后展示的空状态。',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const input = canvasElement.querySelector('input[type="text"]') as HTMLInputElement;
    if (input) {
      input.focus();
      input.value = '不存在的关键字 xyz';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  },
};
