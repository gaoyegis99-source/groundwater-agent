import { mergeConfig } from "vite";
import baseConfig from "../../../services/data/web/vite.config.ts";

export default mergeConfig(baseConfig, {
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/data/v3": {
        target: "http://127.0.0.1:28186",
        changeOrigin: false,
      },
    },
  },
});
