import { defineConfig } from "vitepress"
import { withMermaid } from "vitepress-plugin-mermaid"

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
          type: "image/png",
          href: "/favicon.png",
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
        { text: "开始", link: "/user-guide/getting-started" },
        { text: "插件", link: "/user-guide/plugins/" },
        { text: "开发", link: "/dev-guide/" },
      ],
      sidebar: {
        "/user-guide/": [
          {
            text: "开始",
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
                text: "对话与限制",
                link: "/user-guide/features/interactions",
              },
              {
                text: "自动发帖",
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
              { text: "Bond", link: "/user-guide/plugins/bond" },
              { text: "Iincho", link: "/user-guide/plugins/iincho" },
              { text: "KeyAct", link: "/user-guide/plugins/keyact" },
              { text: "Radar", link: "/user-guide/plugins/radar" },
              { text: "T2X", link: "/user-guide/plugins/t2x" },
              { text: "Topics", link: "/user-guide/plugins/topics" },
              { text: "MLLM", link: "/user-guide/plugins/mllm" },
              { text: "Web", link: "/user-guide/plugins/web" },
            ],
          },
        ],
        "/dev-guide/": [
          {
            text: "开发",
            items: [
              { text: "概览", link: "/dev-guide/" },
              { text: "开发环境", link: "/dev-guide/setup" },
              { text: "架构", link: "/dev-guide/architecture" },
              { text: "Misskey", link: "/dev-guide/misskey" },
              { text: "OpenAI", link: "/dev-guide/openai" },
              { text: "插件开发", link: "/dev-guide/plugins" },
              { text: "测试", link: "/dev-guide/testing" },
              { text: "维护", link: "/dev-guide/maintenance" },
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
    },
    mermaid: {
      theme: "neutral",
    },
  }),
)
