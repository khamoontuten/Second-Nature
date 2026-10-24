import type { ReactNode } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { useProject } from "../api/hooks";
import { useTheme } from "../lib/theme";

export const PAGES = [
  { path: "setup", label: "1 · Project setup" },
  { path: "climate", label: "2 · Climate" },
  { path: "free-cooling", label: "3 · Free cooling" },
  { path: "strategies", label: "4 · Strategies" },
  { path: "calculator", label: "5 · Calculator" },
  { path: "compare", label: "6 · Comparison" },
  { path: "optimize", label: "7 · Optimisation" },
  { path: "report", label: "8 · Report" },
];

export function Layout({ children }: { children: ReactNode }) {
  const { projectId } = useParams();
  const { data: project } = useProject(projectId);
  const { theme, setTheme } = useTheme();
  const search = typeof window !== "undefined" ? window.location.search : "";
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-line bg-surface">
        <div className="max-w-[1500px] mx-auto px-4 py-2.5 flex items-center gap-4">
          <Link to="/" className="flex items-center gap-2 font-semibold text-[15px]" aria-label="Second Nature home">
            <img src="/favicon.svg" alt="" width={22} height={22} />
            Second Nature
          </Link>
          {project && (
            <span className="text-ink-2 text-[13px] truncate">
              / {project.name} <span className="text-ink-3">· v{project.current_version_no}</span>
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden md:inline text-[11px] text-ink-3">Preliminary design &amp; decision support — not for construction</span>
            <select className="input !w-auto !py-1 text-[12px]" value={theme} onChange={(e) => setTheme(e.target.value as any)} aria-label="Colour theme">
              <option value="system">System theme</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
        </div>
        {projectId && (
          <nav className="max-w-[1500px] mx-auto px-4 flex gap-1 overflow-x-auto" aria-label="Project pages">
            {PAGES.map((p) => (
              <NavLink key={p.path} to={`/projects/${projectId}/${p.path}${search}`}
                className={({ isActive }) =>
                  `px-3 py-2 text-[13px] whitespace-nowrap border-b-2 ${isActive ? "border-[var(--seq-500)] font-semibold text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}>
                {p.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>
      <main className="flex-1 w-full max-w-[1500px] mx-auto px-4 py-5">{children}</main>
      <footer className="text-[11px] text-ink-3 text-center py-4 border-t border-line">
        Second Nature · results are screening estimates with stated assumptions; detailed design, CFD, geotechnical study and commissioning remain required.
      </footer>
    </div>
  );
}
