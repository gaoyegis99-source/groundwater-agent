import { mergeConfig } from "vite";
import baseConfig from "../../../web/vite.config.ts";
import { observeDevelopmentHmr } from "./vite-observation.ts";

export default mergeConfig(baseConfig, {
  plugins: [observeDevelopmentHmr()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/v12": {
        target: "http://127.0.0.1:28186",
        changeOrigin: false,
      },
    },
  },
});
