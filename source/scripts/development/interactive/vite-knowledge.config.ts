import { mergeConfig } from "vite";
import baseConfig from "../../../services/knowledge-next/ui/vite.config.ts";
import { observeDevelopmentHmr } from "./vite-observation.ts";

export default mergeConfig(baseConfig, {
  plugins: [observeDevelopmentHmr()],
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      "/knowledge/v3": {
        target: "http://127.0.0.1:28186",
        changeOrigin: false,
      },
      "/data/v3": {
        target: "http://127.0.0.1:28186",
        changeOrigin: false,
      },
    },
  },
});
