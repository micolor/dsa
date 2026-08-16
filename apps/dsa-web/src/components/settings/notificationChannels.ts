import type { NotificationTestChannel } from '../../types/systemConfig';

/**
 * 通知渠道下拉选项的单一真源：设置中心「通知」分类的渠道筛选下拉与「通知测试」面板共用，
 * 保证渠道 value、顺序与中英文 label 两处一致，避免漂移。
 */
export interface NotificationChannelOption {
  value: NotificationTestChannel;
  labelZh: string;
  labelEn: string;
}

export const NOTIFICATION_CHANNEL_OPTIONS: NotificationChannelOption[] = [
  { value: 'wechat', labelZh: '企业微信', labelEn: 'WeCom' },
  { value: 'feishu', labelZh: '飞书', labelEn: 'Feishu' },
  { value: 'dingtalk', labelZh: '钉钉', labelEn: 'DingTalk' },
  { value: 'pushplus', labelZh: 'PushPlus', labelEn: 'PushPlus' },
  { value: 'custom', labelZh: '自定义 Webhook', labelEn: 'Custom Webhook' },
  { value: 'telegram', labelZh: 'Telegram', labelEn: 'Telegram' },
  { value: 'email', labelZh: '邮件', labelEn: 'Email' },
  { value: 'discord', labelZh: 'Discord', labelEn: 'Discord' },
  { value: 'slack', labelZh: 'Slack', labelEn: 'Slack' },
  { value: 'pushover', labelZh: 'Pushover', labelEn: 'Pushover' },
  { value: 'ntfy', labelZh: 'ntfy', labelEn: 'ntfy' },
  { value: 'gotify', labelZh: 'Gotify', labelEn: 'Gotify' },
  { value: 'serverchan3', labelZh: 'ServerChan3', labelEn: 'ServerChan3' },
  { value: 'astrbot', labelZh: 'AstrBot', labelEn: 'AstrBot' },
];
