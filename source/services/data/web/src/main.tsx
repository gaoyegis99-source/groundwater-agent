import '@platform/ui-boundary/workspace-tokens.css';
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { clearLegacyRecoveryKeys } from "./recovery";
import "@platform/operator-ui/styles.css";
import "./style.css";

clearLegacyRecoveryKeys();
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
