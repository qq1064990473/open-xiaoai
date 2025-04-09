import { sleep } from "@mi-gpt/utils";
import { OpenXiaoAI } from "./xiaoai.js";

async function main() {
  await OpenXiaoAI.start({
    openai: {
      model: "gpt-4o-mini",
      baseURL: "https://api.openai.com/v1",
      apiKey: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    },
    async onMessage(_engine, { text }) {
      if (text.startsWith("你好")) {
        await sleep(1000);
        return { text: "你好，很高兴认识你！" };
      }
    },
  });
  process.exit(0);
}

main();
