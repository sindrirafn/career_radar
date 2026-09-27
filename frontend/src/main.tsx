import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import "./applications.css";
import "./workspace.css";
import "./refinements.css";

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
