"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Globe, Zap, Briefcase, BarChart2, Settings, Radio } from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/markets", label: "Markets", icon: Globe },
  { href: "/strategies", label: "Strategies", icon: Zap },
  { href: "/signals", label: "Signals", icon: Radio },
  { href: "/positions", label: "Positions", icon: Briefcase },
  { href: "/performance", label: "Performance", icon: BarChart2 },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 glass border-r border-gold-200/20 flex flex-col min-h-screen z-50">
      <div className="p-6 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="bg-gradient-to-br from-yellow-400 to-yellow-700 rounded-lg w-8 h-8 flex items-center justify-center text-black font-black text-lg shadow-lg shadow-yellow-900/20">
            P
          </div>
          <span className="text-gold text-xl tracking-tight">Prophet</span>
        </div>
      </div>
      <nav className="flex-1 p-3 space-y-2 mt-4">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-300 group ${
                active
                  ? "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Icon className={`h-4 w-4 transition-transform duration-300 group-hover:scale-110 ${active ? "text-yellow-400" : "text-slate-500"}`} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 mt-auto">
        <div className="bg-white/5 rounded-2xl p-4 border border-white/5">
          <p className="text-[10px] text-slate-500 uppercase tracking-widest font-bold mb-1">Status</p>
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)] anim-pulse" />
            <span className="text-xs text-slate-300 font-medium tracking-tight">System Online</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
