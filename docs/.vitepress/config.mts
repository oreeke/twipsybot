import { defineConfig } from "vitepress"
import { withMermaid } from "vitepress-plugin-mermaid"

const currentYear = new Date().getFullYear()

export default withMermaid(
  defineConfig({
    lang: "zh-CN",
    title: "TwipsyBot",
    description: "轻量、可扩展的 Misskey API 机器人",
    cleanUrls: true,
    lastUpdated: true,
    vite: {
      server: {
        watch: {
          usePolling: true,
          interval: 300,
        },
      },
    },
    rewrites: {
      "README.md": "index.md",
      "dev-guide/README.md": "dev-guide/index.md",
      "user-guide/features/README.md": "user-guide/features/index.md",
      "user-guide/plugins/README.md": "user-guide/plugins/index.md",
    },
    head: [
      [
        "link",
        {
          rel: "icon",
          type: "image/svg+xml",
          href: "https://r2-img.oreeke.com/OREEkE-logo.svg",
        },
      ],
      ["meta", { name: "author", content: "OREEkE" }],
      [
        "meta",
        {
          name: "keywords",
          content: "TwipsyBot, Misskey, Bot, OpenAI, 插件, 机器人",
        },
      ],
    ],
    themeConfig: {
      nav: [
        { text: "用户指南", link: "/user-guide/getting-started" },
        { text: "插件", link: "/user-guide/plugins/" },
        { text: "开发", link: "/dev-guide/" },
      ],
      sidebar: {
        "/user-guide/": [
          {
            text: "开始使用",
            items: [
              { text: "快速开始", link: "/user-guide/getting-started" },
              { text: "配置", link: "/user-guide/configuration" },
              { text: "运维", link: "/user-guide/operations" },
              { text: "故障排查", link: "/user-guide/troubleshooting" },
              {
                text: "配置参考",
                link: "/user-guide/reference/configuration",
              },
            ],
          },
          {
            text: "功能",
            link: "/user-guide/features/",
            items: [
              {
                text: "提及、聊天与访问控制",
                link: "/user-guide/features/interactions",
              },
              {
                text: "自动发帖与图片生成",
                link: "/user-guide/features/posting",
              },
              {
                text: "时间线与天线",
                link: "/user-guide/features/timelines",
              },
              {
                text: "管理命令",
                link: "/user-guide/features/admin-commands",
              },
            ],
          },
          {
            text: "插件",
            link: "/user-guide/plugins/",
            items: [
              { text: "Iincho", link: "/user-guide/plugins/iincho" },
              { text: "KeyAct", link: "/user-guide/plugins/keyact" },
              { text: "Radar", link: "/user-guide/plugins/radar" },
              { text: "Topics", link: "/user-guide/plugins/topics" },
              { text: "Vision", link: "/user-guide/plugins/vision" },
            ],
          },
        ],
        "/dev-guide/": [
          {
            text: "开发指南",
            items: [
              { text: "概览", link: "/dev-guide/" },
              { text: "开发环境", link: "/dev-guide/setup" },
              { text: "架构", link: "/dev-guide/architecture" },
              { text: "Misskey", link: "/dev-guide/misskey" },
              { text: "OpenAI", link: "/dev-guide/openai" },
              { text: "插件开发", link: "/dev-guide/plugins" },
              { text: "测试与质量", link: "/dev-guide/testing" },
              { text: "发布与维护", link: "/dev-guide/maintenance" },
            ],
          },
        ],
      },
      socialLinks: [
        { icon: "github", link: "https://github.com/oreeke/twipsybot" },
      ],
      search: { provider: "local" },
      outline: { level: [2, 3], label: "本页内容" },
      lastUpdated: { text: "最后更新" },
      docFooter: { prev: "上一页", next: "下一页" },
      footer: {
        message:
          '基于 <a href="https://github.com/oreeke/twipsybot/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">AGPL-3.0</a> 许可发布',
        copyright:
          `© ${currentYear} <a href="https://github.com/oreeke" target="_blank" rel="noopener noreferrer">OREEkE</a>. All rights reserved.`,
      },
    },
    mermaid: {
      theme: "neutral",
    },
  }),
)
