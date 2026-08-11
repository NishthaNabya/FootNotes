"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
  type CSSProperties,
} from "react";
import * as d3 from "d3-force";
import { AnimatePresence, motion } from "framer-motion";

/* ============================================================
   Types
   ============================================================ */

type Category = "Projects" | "Resources" | "Ideas" | "Archive" | "Unsorted";

interface OrbitNode {
  id: string;
  title: string;
  category: Category;
  content: string;
  tags: string[];
  connections: string[];
}

interface OrbitEdge {
  source: string;
  target: string;
}

// d3-force mutates these in place
interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  title: string;
  category: Category;
  content: string;
  tags: string[];
  connections: string[];
  degree: number;
  radius: number;
}

interface SimEdge {
  source: SimNode | string;
  target: SimNode | string;
}

/* ============================================================
   Mock data — 32 nodes, 4 active categories, dense enough to
   feel populated but readable
   ============================================================ */

const RAW_NODES: OrbitNode[] = [
  // Projects (teal)
  { id: "p1", title: "Orbit · v1 Launch", category: "Projects", tags: ["pkm", "launch", "graph"],
    content: "Ship the first public version of Orbit — a knowledge graph that feels like a floating brain rather than a folder tree. Aiming for May 2026.\n\n\"The point isn't to organize notes. The point is to see the shape of what you've been thinking about for the last six months.\"",
    connections: ["p2","p3","i1","i2","i3","i4","r1","r2","r3","r4","r5","r6"] },
  { id: "p2", title: "Garden Notes Migration", category: "Projects", tags: ["migration","notes"],
    content: "Move 800+ notes from Notion into the Orbit format. Preserve backlinks, normalize tags, archive anything older than two years that hasn't been touched.",
    connections: ["p1","p3","i5","r1"] },
  { id: "p3", title: "Daily Review System", category: "Projects", tags: ["habit","review"],
    content: "A 10-minute end-of-day ritual: skim today's captures, promote two to evergreen, link at least one back to an existing thread.",
    connections: ["p1","p2","i6","i7","r4"] },
  { id: "p4", title: "Side Project: Compass", category: "Projects", tags: ["side","navigation"],
    content: "A spatial bookmark manager. Think arc.net's spaces, but for ideas instead of tabs.",
    connections: ["i8","r7","r8"] },
  { id: "p5", title: "Writing Habit", category: "Projects", tags: ["writing","habit"],
    content: "300 words daily. No quality bar. The compounding interest of writing is the only thing that's ever worked for me.",
    connections: ["i9","i10","r9"] },
  { id: "p6", title: "Reading List 2026", category: "Projects", tags: ["reading"],
    content: "Twelve books, one per month. Mix of fiction and tools-for-thought non-fiction. No technical books — those are for evenings, not weekends.",
    connections: ["r1","r2","r5","r10"] },

  // Ideas (amber)
  { id: "i1", title: "Notes as living organisms", category: "Ideas", tags: ["metaphor","pkm"],
    content: "Notes don't sit on a shelf — they grow, get pruned, fork, sometimes die. A note system should reflect that lifecycle, not pretend everything is permanent.",
    connections: ["p1","i2","i3","r1","r3"] },
  { id: "i2", title: "Atomic notes vs. evergreen", category: "Ideas", tags: ["pkm","theory"],
    content: "Atomic = one idea per note. Evergreen = continuously refined. They're not the same thing. Most people conflate them and end up with neither.",
    connections: ["p1","i1","r3","r4"] },
  { id: "i3", title: "Friction as a feature", category: "Ideas", tags: ["design"],
    content: "Sometimes you want capture to be hard. Friction is how you filter low-quality thoughts before they pollute the graph.",
    connections: ["p1","i1","i7","r6"] },
  { id: "i4", title: "The shape of a thought", category: "Ideas", tags: ["metaphor"],
    content: "Every idea has a shape — narrow and deep, wide and shallow, tangled, linear. Visualizing the shape tells you what kind of thinking you've been doing lately.",
    connections: ["p1","i1","i6","r2"] },
  { id: "i5", title: "Why links beat folders", category: "Ideas", tags: ["pkm","theory"],
    content: "Folders force a single hierarchy. Links allow many. The world has many. Folders lose, every time, eventually.",
    connections: ["p2","i1","i2","r1"] },
  { id: "i6", title: "Memory as a constellation", category: "Ideas", tags: ["metaphor","memory"],
    content: "You don't remember a fact, you remember a fact in the company of other facts. Isolate it and it dims.",
    connections: ["p3","i4","i9","r3"] },
  { id: "i7", title: "Slow thinking, fast tools", category: "Ideas", tags: ["tools"],
    content: "The tool should be instant. The thinking shouldn't be. Most note apps get this exactly backwards.",
    connections: ["p3","i3","r6","r7"] },
  { id: "i8", title: "Spatial memory in software", category: "Ideas", tags: ["spatial","ux"],
    content: "We remember where things are on a desk. We don't remember where things are in a database. Software that pretends otherwise is fighting the brain.",
    connections: ["p4","i4","r7","r8"] },
  { id: "i9", title: "The compounding interest of writing", category: "Ideas", tags: ["writing"],
    content: "One sentence a day for ten years is a book. The math is uncomfortable because it works.",
    connections: ["p5","i6","i10","r9"] },
  { id: "i10", title: "Curiosity is a renewable fuel", category: "Ideas", tags: ["motivation"],
    content: "Discipline burns out. Curiosity refills overnight if you let it. Build systems that protect curiosity, not ones that demand discipline.",
    connections: ["p5","i9","r9","r10"] },

  // Resources (blue)
  { id: "r1", title: "How to Take Smart Notes", category: "Resources", tags: ["book","zettelkasten"],
    content: "Sönke Ahrens. The book that made zettelkasten legible to people outside German academia. Worth re-reading every two years.",
    connections: ["p1","p2","p6","i1","i2","i5"] },
  { id: "r2", title: "Building a Second Brain", category: "Resources", tags: ["book","pkm"],
    content: "Tiago Forte. CODE and PARA. A more pragmatic take than Ahrens, less rigorous, more useful day-to-day.",
    connections: ["p1","p6","i4","r3"] },
  { id: "r3", title: "Andy Matuschak · Evergreen Notes", category: "Resources", tags: ["essay","evergreen"],
    content: "The canonical source on evergreen notes. His own working notes are the best demonstration of the method.",
    connections: ["p1","i1","i2","i6","r4"] },
  { id: "r4", title: "Memex — As We May Think", category: "Resources", tags: ["essay","history"],
    content: "Vannevar Bush, 1945. The piece that imagined hypertext before hypertext existed. Still ahead of most current tools.",
    connections: ["p1","p3","i2","r3","r5"] },
  { id: "r5", title: "Engelbart · Augmenting Intellect", category: "Resources", tags: ["essay","history"],
    content: "1962. Tools shape thought. Thought shapes tools. We are responsible for the loop.",
    connections: ["p1","p6","r4","r6"] },
  { id: "r6", title: "Tools for Thought", category: "Resources", tags: ["book","essay"],
    content: "Howard Rheingold. Profiles of the people who built the foundation. Reads like a family tree of every PKM tool you've ever used.",
    connections: ["p1","i3","i7","r5"] },
  { id: "r7", title: "A Pattern Language", category: "Resources", tags: ["book","design"],
    content: "Christopher Alexander. Not about software, but every piece of software design owes it a debt. Patterns as a unit of knowledge.",
    connections: ["p4","i7","i8","r8"] },
  { id: "r8", title: "Borges · Library of Babel", category: "Resources", tags: ["fiction","metaphor"],
    content: "Every possible book exists. The problem isn't writing. The problem is finding. Same for notes.",
    connections: ["p4","i8","r7"] },
  { id: "r9", title: "Roam Research blog archive", category: "Resources", tags: ["blog","pkm"],
    content: "The early Roam essays — graph-based note taking when the rest of the world was still doing folders.",
    connections: ["p5","i9","i10","r10"] },
  { id: "r10", title: "The Memex Method", category: "Resources", tags: ["essay","method"],
    content: "Cory Doctorow's working method. Capture wide, write narrow. Inputs need to outweigh outputs by a healthy margin.",
    connections: ["p6","i10","r9"] },

  // Archive (gray) — orphans and old threads
  { id: "a1", title: "Old Roam vault export", category: "Archive", tags: ["archive"],
    content: "Exported from Roam in 2023. Kept for reference, not actively maintained.", connections: ["a2"] },
  { id: "a2", title: "Defunct: Newsletter drafts", category: "Archive", tags: ["archive","writing"],
    content: "Three unfinished newsletter drafts from 2024. Probably never publishing these but the ideas are reusable.", connections: ["a1","a3"] },
  { id: "a3", title: "Scratch ideas Q4 2024", category: "Archive", tags: ["archive"],
    content: "Loose captures from late 2024. Some promoted to evergreen, the rest live here.", connections: ["a2","a4"] },
  { id: "a4", title: "Old project: Lighthouse", category: "Archive", tags: ["archive","project"],
    content: "Wound down in 2025. Leaving notes for posterity.", connections: ["a3","a5"] },
  { id: "a5", title: "Migrated: pre-Notion notes", category: "Archive", tags: ["archive"],
    content: "Apple Notes export from before the Notion era. Mostly recipes and reading notes.", connections: ["a4","a6"] },
  { id: "a6", title: "Frozen: thesis outline", category: "Archive", tags: ["archive","writing"],
    content: "Senior thesis outline. Probably not coming back to this but useful as a historical artifact.", connections: ["a5"] },
];

// Build edges from connections (deduplicated, undirected)
const RAW_EDGES: OrbitEdge[] = (() => {
  const seen = new Set<string>();
  const edges: OrbitEdge[] = [];
  RAW_NODES.forEach((n) => {
    n.connections.forEach((c) => {
      const key = [n.id, c].sort().join("→");
      if (!seen.has(key) && RAW_NODES.find((m) => m.id === c)) {
        seen.add(key);
        edges.push({ source: n.id, target: c });
      }
    });
  });
  return edges;
})();

/* ============================================================
   Category styling — matches the screenshot palette
   ============================================================ */

const CATEGORY_STYLE: Record<
  Category,
  { fill: string; glow: string; ring: string; label: string }
> = {
  Projects: { fill: "#34d8b0", glow: "rgba(52, 216, 176, 0.55)", ring: "rgba(52, 216, 176, 0.35)", label: "Projects" },
  Resources: { fill: "#5b8cff", glow: "rgba(91, 140, 255, 0.55)", ring: "rgba(91, 140, 255, 0.35)", label: "Resources" },
  Ideas: { fill: "#f5b443", glow: "rgba(245, 180, 67, 0.55)", ring: "rgba(245, 180, 67, 0.35)", label: "Ideas" },
  Archive: { fill: "#7a8694", glow: "rgba(122, 134, 148, 0.45)", ring: "rgba(122, 134, 148, 0.30)", label: "Archive" },
  Unsorted: { fill: "#e8ecf2", glow: "rgba(232, 236, 242, 0.40)", ring: "rgba(232, 236, 242, 0.25)", label: "Unsorted" },
};

/* ============================================================
   Background particle drift — pure canvas, very cheap
   ============================================================ */

function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let running = true;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
    };
    resize();
    window.addEventListener("resize", resize);

    const COUNT = 90;
    const particles = Array.from({ length: COUNT }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.08 * dpr,
      vy: (Math.random() - 0.5) * 0.08 * dpr,
      r: (Math.random() * 0.9 + 0.3) * dpr,
      a: Math.random() * 0.5 + 0.1,
    }));

    const tick = () => {
      if (!running) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = canvas.width;
        if (p.x > canvas.width) p.x = 0;
        if (p.y < 0) p.y = canvas.height;
        if (p.y > canvas.height) p.y = 0;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(200, 220, 255, ${p.a})`;
        ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 h-full w-full pointer-events-none"
      style={{ opacity: 0.6 }}
    />
  );
}

/* ============================================================
   Main component
   ============================================================ */

export default function OrbitMind() {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const simRef = useRef<d3.Simulation<SimNode, undefined> | null>(null);

  const [dims, setDims] = useState({ w: 1200, h: 800 });

  // Build sim nodes/edges once
  const { simNodes, simEdges } = useMemo(() => {
    const degree: Record<string, number> = {};
    RAW_NODES.forEach((n) => (degree[n.id] = 0));
    RAW_EDGES.forEach((e) => {
      degree[e.source] = (degree[e.source] ?? 0) + 1;
      degree[e.target] = (degree[e.target] ?? 0) + 1;
    });
    const maxDeg = Math.max(...Object.values(degree), 1);
    const nodes: SimNode[] = RAW_NODES.map((n) => {
      const deg = degree[n.id] ?? 0;
      // Scale radius non-linearly so the most-connected nodes pop
      const radius = 7 + 14 * Math.pow(deg / maxDeg, 0.7);
      return {
        ...n,
        degree: deg,
        radius,
      };
    });
    const edges: SimEdge[] = RAW_EDGES.map((e) => ({
      source: e.source,
      target: e.target,
    }));
    return { simNodes: nodes, simEdges: edges };
  }, []);

  // Track positions in state so React re-renders on each tick
  const [, forceRender] = useState(0);
  const tickCount = useRef(0);

  // Resize observer
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setDims({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Boot the d3 force simulation
  useEffect(() => {
    if (!dims.w || !dims.h) return;

    const sim = d3
      .forceSimulation<SimNode>(simNodes)
      .force(
        "link",
        d3
          .forceLink<SimNode, SimEdge>(simEdges)
          .id((d) => d.id)
          .distance(80)
          .strength(0.18)
      )
      .force("charge", d3.forceManyBody<SimNode>().strength(-180))
      .force("center", d3.forceCenter(dims.w / 2, dims.h / 2).strength(0.06))
      .force(
        "collide",
        d3.forceCollide<SimNode>().radius((d) => d.radius + 14).strength(0.85)
      )
      .alphaDecay(0.015)
      .velocityDecay(0.35);

    sim.on("tick", () => {
      tickCount.current += 1;
      // Throttle re-render — every 2 ticks is plenty smooth
      if (tickCount.current % 2 === 0) {
        forceRender((n) => n + 1);
      }
    });

    simRef.current = sim;
    return () => {
      sim.stop();
      simRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dims.w, dims.h]);

  /* ----------------------------------------------------------
     Selection / focus / hover state
     ---------------------------------------------------------- */
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [breadcrumb, setBreadcrumb] = useState<string[]>([]);
  const [focusedNeighborhood, setFocusedNeighborhood] = useState<Set<string> | null>(null);

  // Pan / zoom (manual — no d3-zoom dependency)
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const isPanning = useRef(false);
  const panStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });

  const onMouseDown = (e: React.MouseEvent) => {
    // only pan on empty space — node clicks stop propagation
    if ((e.target as Element).tagName === "svg" || (e.target as Element).classList.contains("orbit-bg-rect")) {
      isPanning.current = true;
      panStart.current = { x: e.clientX, y: e.clientY, tx: transform.x, ty: transform.y };
    }
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!isPanning.current) return;
    setTransform((t) => ({
      ...t,
      x: panStart.current.tx + (e.clientX - panStart.current.x),
      y: panStart.current.ty + (e.clientY - panStart.current.y),
    }));
  };
  const onMouseUp = () => {
    isPanning.current = false;
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const delta = -e.deltaY * 0.0015;
    setTransform((t) => {
      const nextK = Math.min(3, Math.max(0.4, t.k * (1 + delta)));
      // Zoom toward cursor
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return { ...t, k: nextK };
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const ratio = nextK / t.k;
      return {
        x: mx - (mx - t.x) * ratio,
        y: my - (my - t.y) * ratio,
        k: nextK,
      };
    });
  };

  // Build neighborhood lookup (for orbit pull / fade)
  const neighbors = useMemo(() => {
    const map = new Map<string, Set<string>>();
    RAW_NODES.forEach((n) => map.set(n.id, new Set()));
    RAW_EDGES.forEach((e) => {
      map.get(e.source)?.add(e.target);
      map.get(e.target)?.add(e.source);
    });
    return map;
  }, []);

  // BFS for k-degree neighborhood
  const neighborhood = useCallback(
    (id: string, depth: number): Set<string> => {
      const out = new Set<string>([id]);
      let frontier = new Set<string>([id]);
      for (let d = 0; d < depth; d++) {
        const next = new Set<string>();
        frontier.forEach((nid) => {
          neighbors.get(nid)?.forEach((nb) => {
            if (!out.has(nb)) {
              out.add(nb);
              next.add(nb);
            }
          });
        });
        frontier = next;
      }
      return out;
    },
    [neighbors]
  );

  // When a node is selected, gently pull its neighbors closer using
  // a custom positioning force re-run
  useEffect(() => {
    const sim = simRef.current;
    if (!sim) return;

    if (!selectedId) {
      // Reset link distance
      const linkF = sim.force<d3.ForceLink<SimNode, SimEdge>>("link");
      if (linkF) linkF.distance(80);
      sim.alpha(0.4).restart();
      return;
    }

    const linkF = sim.force<d3.ForceLink<SimNode, SimEdge>>("link");
    if (linkF) {
      linkF.distance((l: any) => {
        const sId = typeof l.source === "string" ? l.source : l.source.id;
        const tId = typeof l.target === "string" ? l.target : l.target.id;
        const involves = sId === selectedId || tId === selectedId;
        return involves ? 55 : 95;
      });
    }
    sim.alpha(0.5).restart();
  }, [selectedId]);

  // Search matches
  const searchMatches = useMemo(() => {
    if (!search.trim()) return null;
    const q = search.toLowerCase();
    const matches = new Set<string>();
    simNodes.forEach((n) => {
      if (
        n.title.toLowerCase().includes(q) ||
        n.tags.some((t) => t.toLowerCase().includes(q)) ||
        n.content.toLowerCase().includes(q)
      ) {
        matches.add(n.id);
      }
    });
    return matches;
  }, [search, simNodes]);

  // Determine visual emphasis for each node
  const getEmphasis = (id: string): "highlight" | "dim" | "normal" => {
    if (focusedNeighborhood) {
      return focusedNeighborhood.has(id) ? "highlight" : "dim";
    }
    if (searchMatches) {
      return searchMatches.has(id) ? "highlight" : "dim";
    }
    if (selectedId) {
      if (id === selectedId) return "highlight";
      const nbs = neighbors.get(selectedId);
      return nbs?.has(id) ? "highlight" : "dim";
    }
    return "normal";
  };

  const counts = useMemo(() => {
    const c: Record<Category, number> = {
      Projects: 0, Resources: 0, Ideas: 0, Archive: 0, Unsorted: 0,
    };
    simNodes.forEach((n) => (c[n.category] += 1));
    return c;
  }, [simNodes]);

  // Breadcrumb push
  const handleSelectNode = (id: string) => {
    setSelectedId(id);
    setBreadcrumb((bc) => {
      if (bc[bc.length - 1] === id) return bc;
      return [...bc, id];
    });
  };

  const handleOpenDrawer = (id: string) => {
    setDrawerId(id);
    handleSelectNode(id);
  };

  const handleDrillDown = (id: string) => {
    const nb = neighborhood(id, 2);
    setFocusedNeighborhood(nb);
    setSelectedId(id);
  };

  const handleClearFocus = () => {
    setFocusedNeighborhood(null);
  };

  const handleBreadcrumbJump = (idx: number) => {
    const id = breadcrumb[idx];
    setBreadcrumb(breadcrumb.slice(0, idx + 1));
    setSelectedId(id);
    setFocusedNeighborhood(null);
  };

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedId(null);
        setDrawerId(null);
        setFocusedNeighborhood(null);
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const input = document.getElementById("orbit-search") as HTMLInputElement | null;
        input?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const drawerNode = drawerId ? simNodes.find((n) => n.id === drawerId) : null;

  /* ============================================================
     Render
     ============================================================ */

  return (
    <div
      ref={containerRef}
      className="relative w-full h-screen overflow-hidden font-sans select-none"
      style={{
        background:
          "radial-gradient(ellipse at 70% 80%, rgba(52,216,176,0.06) 0%, transparent 55%), radial-gradient(ellipse at 20% 20%, rgba(91,140,255,0.05) 0%, transparent 55%), #0a0e1a",
        fontFamily:
          "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      }}
    >
      <ParticleField />

      {/* Top bar: logo + search + counts */}
      <div className="absolute top-0 left-0 right-0 z-20 flex items-start justify-between px-6 pt-5 pointer-events-none">
        {/* Logo */}
        <div className="flex items-center gap-3 pointer-events-auto">
          <div className="relative w-12 h-12 flex items-center justify-center">
            <div
              className="absolute inset-0 rounded-full"
              style={{
                background:
                  "radial-gradient(circle, rgba(91,140,255,0.9) 0%, rgba(91,140,255,0.2) 60%, transparent 80%)",
                filter: "blur(2px)",
              }}
            />
            <div
              className="absolute w-8 h-8 rounded-full"
              style={{
                background:
                  "radial-gradient(circle at 35% 35%, #9bb8ff 0%, #3a5ea8 70%, #1a2b52 100%)",
                boxShadow: "0 0 18px rgba(91,140,255,0.6)",
              }}
            />
            <div
              className="absolute w-12 h-3 rounded-full border opacity-60"
              style={{
                borderColor: "rgba(155, 184, 255, 0.5)",
                transform: "rotate(-18deg)",
                borderWidth: "1px",
                background: "transparent",
              }}
            />
          </div>
          <div className="leading-tight">
            <div className="text-white tracking-[0.3em] text-sm font-medium">
              ORBIT
            </div>
            <div className="text-[11px] tracking-[0.2em] text-white/40 lowercase">
              mind graph
            </div>
          </div>
        </div>

        {/* Search */}
        <div className="flex-1 max-w-xl mx-8 pointer-events-auto">
          <div
            className="flex items-center gap-2 rounded-full px-4 py-2.5 backdrop-blur-md"
            style={{
              background: "rgba(15, 22, 38, 0.6)",
              border: "1px solid rgba(255,255,255,0.06)",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-white/40">
              <circle cx="11" cy="11" r="7" strokeWidth="2" />
              <path d="m20 20-3.5-3.5" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <input
              id="orbit-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search the graph…"
              className="bg-transparent flex-1 text-sm text-white/90 placeholder:text-white/30 outline-none"
            />
            <span className="text-[10px] text-white/40 px-1.5 py-0.5 rounded border border-white/10">
              ⌘K
            </span>
          </div>
        </div>

        {/* Counts */}
        <div className="flex items-center gap-4 text-xs text-white/50 font-mono pt-3 pointer-events-auto">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" style={{ boxShadow: "0 0 6px #34d8b0" }} />
            <span>{simNodes.length} nodes</span>
          </div>
          <span className="text-white/20">·</span>
          <span>{simEdges.length} links</span>
        </div>
      </div>

      {/* Breadcrumb */}
      {breadcrumb.length > 0 && (
        <div className="absolute top-20 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 text-xs text-white/50 font-mono backdrop-blur-md px-3 py-1.5 rounded-full"
             style={{ background: "rgba(15,22,38,0.5)", border: "1px solid rgba(255,255,255,0.06)" }}>
          <button
            onClick={() => { setBreadcrumb([]); setSelectedId(null); setFocusedNeighborhood(null); }}
            className="hover:text-white/90 transition"
          >
            home
          </button>
          {breadcrumb.map((id, i) => {
            const n = simNodes.find((x) => x.id === id);
            return (
              <span key={i} className="flex items-center gap-2">
                <span className="text-white/20">/</span>
                <button
                  onClick={() => handleBreadcrumbJump(i)}
                  className={`transition ${i === breadcrumb.length - 1 ? "text-white/90" : "hover:text-white/90"}`}
                >
                  {n?.title.length && n.title.length > 24 ? n.title.slice(0, 22) + "…" : n?.title}
                </button>
              </span>
            );
          })}
        </div>
      )}

      {/* Categories panel */}
      <div
        className="absolute left-6 top-1/2 -translate-y-1/2 z-20 rounded-2xl px-5 py-4 backdrop-blur-md min-w-[200px]"
        style={{
          background: "rgba(15, 22, 38, 0.55)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <div className="text-[10px] tracking-[0.25em] text-white/40 mb-3 font-medium">
          CATEGORIES
        </div>
        <div className="space-y-2.5">
          {(["Projects", "Ideas", "Resources", "Archive"] as Category[]).map((cat) => (
            <div key={cat} className="flex items-center justify-between gap-4 text-sm">
              <div className="flex items-center gap-2.5">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: CATEGORY_STYLE[cat].fill,
                    boxShadow: `0 0 8px ${CATEGORY_STYLE[cat].glow}`,
                  }}
                />
                <span className="text-white/85">{cat}</span>
              </div>
              <span className="text-white/35 font-mono text-xs">{counts[cat]}</span>
            </div>
          ))}
        </div>
        <div className="mt-4 pt-4 border-t border-white/5 space-y-1.5 text-[11px] font-mono text-white/40">
          <div className="flex justify-between">
            <span>Density</span>
            <span>
              {(simEdges.length / Math.max(simNodes.length, 1)).toFixed(2)} avg
            </span>
          </div>
          <div className="flex justify-between">
            <span>Last sync</span>
            <span>2m ago</span>
          </div>
        </div>
      </div>

      {/* Focus banner */}
      {focusedNeighborhood && (
        <div className="absolute bottom-24 left-1/2 -translate-x-1/2 z-20 flex items-center gap-3 text-xs text-white/70 font-mono backdrop-blur-md px-4 py-2 rounded-full"
             style={{ background: "rgba(15,22,38,0.6)", border: "1px solid rgba(52,216,176,0.25)" }}>
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
          focused · {focusedNeighborhood.size} nodes
          <button onClick={handleClearFocus} className="text-white/50 hover:text-white/90 ml-1">
            clear
          </button>
        </div>
      )}

      {/* SVG graph */}
      <svg
        ref={svgRef}
        width={dims.w}
        height={dims.h}
        className="absolute inset-0"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
        style={{ cursor: isPanning.current ? "grabbing" : "grab" }}
      >
        {/* invisible background for pan capture */}
        <rect
          className="orbit-bg-rect"
          x={0}
          y={0}
          width={dims.w}
          height={dims.h}
          fill="transparent"
        />

        <defs>
          {(["Projects", "Resources", "Ideas", "Archive", "Unsorted"] as Category[]).map((cat) => (
            <radialGradient key={cat} id={`grad-${cat}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={CATEGORY_STYLE[cat].fill} stopOpacity="1" />
              <stop offset="60%" stopColor={CATEGORY_STYLE[cat].fill} stopOpacity="0.6" />
              <stop offset="100%" stopColor={CATEGORY_STYLE[cat].fill} stopOpacity="0" />
            </radialGradient>
          ))}
          <filter id="node-glow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
        </defs>

        <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.k})`}>
          {/* Edges */}
          <g>
            {simEdges.map((e, i) => {
              const s = (typeof e.source === "string"
                ? simNodes.find((n) => n.id === e.source)
                : (e.source as SimNode)) as SimNode | undefined;
              const t = (typeof e.target === "string"
                ? simNodes.find((n) => n.id === e.target)
                : (e.target as SimNode)) as SimNode | undefined;
              if (!s || !t || s.x == null || t.x == null) return null;

              const sEmph = getEmphasis(s.id);
              const tEmph = getEmphasis(t.id);
              const involvesHighlight = sEmph === "highlight" || tEmph === "highlight";
              const eitherDim = sEmph === "dim" || tEmph === "dim";
              const both = sEmph === "highlight" && tEmph === "highlight";

              let opacity = 0.18;
              let stroke = "rgba(255,255,255,0.6)";
              if (selectedId && (s.id === selectedId || t.id === selectedId)) {
                opacity = 0.7;
                stroke = CATEGORY_STYLE[simNodes.find((n) => n.id === selectedId)!.category].fill;
              } else if (both) {
                opacity = 0.45;
              } else if (involvesHighlight && !eitherDim) {
                opacity = 0.32;
              } else if (eitherDim) {
                opacity = 0.04;
              }

              return (
                <line
                  key={i}
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  stroke={stroke}
                  strokeOpacity={opacity}
                  strokeWidth={selectedId && (s.id === selectedId || t.id === selectedId) ? 1.2 : 0.6}
                />
              );
            })}
          </g>

          {/* Nodes */}
          <g>
            {simNodes.map((n) => {
              if (n.x == null || n.y == null) return null;
              const style = CATEGORY_STYLE[n.category];
              const emph = getEmphasis(n.id);
              const isHover = hoverId === n.id;
              const isSelected = selectedId === n.id;
              const baseOpacity = emph === "dim" ? 0.18 : 1;
              const r = n.radius * (isSelected ? 1.25 : isHover ? 1.12 : 1);

              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x}, ${n.y})`}
                  style={{ cursor: "pointer", opacity: baseOpacity, transition: "opacity 0.3s" }}
                  onMouseEnter={() => setHoverId(n.id)}
                  onMouseLeave={() => setHoverId((h) => (h === n.id ? null : h))}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSelectNode(n.id);
                  }}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    handleOpenDrawer(n.id);
                  }}
                >
                  {/* outer glow */}
                  <circle
                    r={r * 2.6}
                    fill={`url(#grad-${n.category})`}
                    opacity={isSelected ? 0.9 : isHover ? 0.7 : 0.45}
                    style={{ transition: "opacity 0.25s" }}
                  />
                  {/* ring */}
                  <circle
                    r={r * 1.5}
                    fill="none"
                    stroke={style.ring}
                    strokeWidth={1}
                    opacity={isSelected ? 0.9 : 0.5}
                  />
                  {/* core */}
                  <circle
                    r={r * 0.55}
                    fill={style.fill}
                    style={{
                      filter: `drop-shadow(0 0 ${isSelected ? 14 : 6}px ${style.glow})`,
                      transition: "filter 0.25s",
                    }}
                  />
                  {/* selected outer halo */}
                  {isSelected && (
                    <circle
                      r={r * 1.9}
                      fill="none"
                      stroke="rgba(255,255,255,0.85)"
                      strokeWidth={1}
                      opacity={0.9}
                    />
                  )}
                </g>
              );
            })}
          </g>

          {/* Labels — render after nodes so they sit above */}
          <g style={{ pointerEvents: "none" }}>
            {simNodes.map((n) => {
              if (n.x == null || n.y == null) return null;
              const emph = getEmphasis(n.id);
              const isHover = hoverId === n.id;
              const isSelected = selectedId === n.id;
              if (emph === "dim" && !isHover) return null;
              const opacity = isHover || isSelected ? 1 : emph === "highlight" ? 0.85 : 0.55;
              const fontSize = isSelected ? 13 : 11;
              const labelText = n.title.length > 32 ? n.title.slice(0, 30) + "…" : n.title;
              return (
                <text
                  key={n.id}
                  x={n.x}
                  y={n.y + n.radius + 14}
                  textAnchor="middle"
                  fill="white"
                  fillOpacity={opacity}
                  fontSize={fontSize}
                  fontWeight={isSelected ? 500 : 400}
                  style={{
                    transition: "fill-opacity 0.25s",
                    paintOrder: "stroke",
                    stroke: "#0a0e1a",
                    strokeWidth: 3,
                    strokeOpacity: 0.6,
                    strokeLinejoin: "round",
                  }}
                >
                  {labelText}
                </text>
              );
            })}
          </g>
        </g>
      </svg>

      {/* Bottom bar */}
      <div className="absolute bottom-5 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 backdrop-blur-md rounded-full px-2 py-1.5"
           style={{ background: "rgba(15,22,38,0.6)", border: "1px solid rgba(255,255,255,0.06)" }}>
        <button
          onClick={() => simRef.current?.alpha(0.6).restart()}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs text-white/80 hover:bg-white/5 transition"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" strokeLinecap="round" />
          </svg>
          Physics
        </button>
        <button
          onClick={() => setTransform({ x: 0, y: 0, k: 1 })}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs text-white/80 hover:bg-white/5 transition"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="3" y1="6" x2="21" y2="6" strokeLinecap="round" />
            <line x1="3" y1="12" x2="21" y2="12" strokeLinecap="round" />
            <line x1="3" y1="18" x2="21" y2="18" strokeLinecap="round" />
          </svg>
          Labels
        </button>
        <div className="w-px h-5 bg-white/10 mx-1" />
        <button
          onClick={() => setTransform((t) => ({ ...t, k: Math.min(3, t.k * 1.2) }))}
          className="px-2.5 py-1.5 rounded-full text-white/70 hover:bg-white/5 transition"
          aria-label="Zoom in"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" strokeLinecap="round" />
            <path d="M11 8v6M8 11h6" strokeLinecap="round" />
          </svg>
        </button>
        <button
          onClick={() => setTransform((t) => ({ ...t, k: Math.max(0.4, t.k / 1.2) }))}
          className="px-2.5 py-1.5 rounded-full text-white/70 hover:bg-white/5 transition"
          aria-label="Zoom out"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" strokeLinecap="round" />
            <path d="M8 11h6" strokeLinecap="round" />
          </svg>
        </button>
        <button
          onClick={() => { setTransform({ x: 0, y: 0, k: 1 }); simRef.current?.alpha(0.5).restart(); }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs text-white/80 hover:bg-white/5 transition"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3" strokeLinecap="round" />
          </svg>
          Recenter
        </button>
      </div>

      {/* Hover tooltip */}
      {hoverId && (() => {
        const n = simNodes.find((x) => x.id === hoverId);
        if (!n || n.x == null || n.y == null) return null;
        const screenX = n.x * transform.k + transform.x;
        const screenY = n.y * transform.k + transform.y;
        const style: CSSProperties = {
          left: screenX + 18,
          top: screenY - 12,
        };
        return (
          <div
            className="absolute z-30 pointer-events-none px-3 py-1.5 rounded-lg text-xs backdrop-blur-md"
            style={{
              ...style,
              background: "rgba(15,22,38,0.85)",
              border: "1px solid rgba(255,255,255,0.08)",
              color: "white",
            }}
          >
            <div className="flex items-center gap-2">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: CATEGORY_STYLE[n.category].fill }}
              />
              <span className="font-medium">{n.title}</span>
            </div>
            <div className="text-[10px] text-white/40 mt-0.5 font-mono">
              {n.degree} connections
            </div>
          </div>
        );
      })()}

      {/* Hint */}
      <div className="absolute bottom-5 right-6 z-20 text-[10px] font-mono text-white/30 leading-relaxed text-right">
        <div>drag <span className="text-white/50">empty space to pan</span> · scroll <span className="text-white/50">to zoom</span></div>
        <div>click <span className="text-white/50">a node to focus</span> · double-click <span className="text-white/50">to open</span> · esc <span className="text-white/50">to release</span></div>
      </div>

      {/* Drawer */}
      <AnimatePresence>
        {drawerNode && (
          <motion.div
            key="drawer"
            initial={{ x: 480, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 480, opacity: 0 }}
            transition={{ type: "spring", damping: 30, stiffness: 240 }}
            className="absolute top-0 right-0 bottom-0 z-30 w-[460px] flex flex-col"
            style={{
              background: "linear-gradient(180deg, rgba(15,22,38,0.95) 0%, rgba(10,14,26,0.95) 100%)",
              borderLeft: "1px solid rgba(255,255,255,0.06)",
              backdropFilter: "blur(20px)",
            }}
          >
            <div className="flex items-start justify-between px-7 pt-7 pb-4">
              <div>
                <div
                  className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[10px] tracking-[0.2em] uppercase font-medium mb-3"
                  style={{
                    background: `${CATEGORY_STYLE[drawerNode.category].fill}15`,
                    color: CATEGORY_STYLE[drawerNode.category].fill,
                    border: `1px solid ${CATEGORY_STYLE[drawerNode.category].fill}30`,
                  }}
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{ background: CATEGORY_STYLE[drawerNode.category].fill }}
                  />
                  {drawerNode.category.slice(0, -1)}
                </div>
                <h2 className="text-2xl font-medium text-white leading-tight">
                  {drawerNode.title}
                </h2>
                <div className="flex items-center gap-3 mt-2 text-[11px] font-mono text-white/40">
                  <span>Apr 2026</span>
                  <span className="text-white/15">·</span>
                  <span>{drawerNode.connections.length} connections</span>
                  <span className="text-white/15">·</span>
                  <span>{drawerNode.content.split(/\s+/).length} words</span>
                </div>
              </div>
              <button
                onClick={() => setDrawerId(null)}
                className="w-8 h-8 rounded-full flex items-center justify-center text-white/50 hover:text-white/90 hover:bg-white/5 transition"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="6" y1="6" x2="18" y2="18" strokeLinecap="round" />
                  <line x1="18" y1="6" x2="6" y2="18" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-7 pb-6 space-y-6">
              {/* Content */}
              <section>
                <div className="text-[10px] tracking-[0.25em] text-white/35 font-medium mb-2">
                  CONTENT
                </div>
                <div className="text-[14px] leading-relaxed text-white/80 whitespace-pre-line">
                  {drawerNode.content}
                </div>
              </section>

              {/* Tags */}
              {drawerNode.tags.length > 0 && (
                <section>
                  <div className="text-[10px] tracking-[0.25em] text-white/35 font-medium mb-2">
                    TAGS
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {drawerNode.tags.map((t) => (
                      <button
                        key={t}
                        onClick={() => setSearch(t)}
                        className="px-2.5 py-1 rounded-md text-[11px] font-mono text-white/60 hover:text-white/90 transition"
                        style={{
                          background: "rgba(255,255,255,0.04)",
                          border: "1px solid rgba(255,255,255,0.06)",
                        }}
                      >
                        #{t}
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {/* Connections */}
              <section>
                <div className="text-[10px] tracking-[0.25em] text-white/35 font-medium mb-2">
                  CONNECTIONS ({drawerNode.connections.length})
                </div>
                <div className="space-y-1.5">
                  {drawerNode.connections.map((cid) => {
                    const c = simNodes.find((x) => x.id === cid);
                    if (!c) return null;
                    return (
                      <button
                        key={cid}
                        onClick={() => handleOpenDrawer(cid)}
                        className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-left transition group"
                        style={{
                          background: "rgba(255,255,255,0.02)",
                          border: "1px solid rgba(255,255,255,0.05)",
                        }}
                      >
                        <div className="flex items-center gap-2.5">
                          <span
                            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                            style={{
                              background: CATEGORY_STYLE[c.category].fill,
                              boxShadow: `0 0 6px ${CATEGORY_STYLE[c.category].glow}`,
                            }}
                          />
                          <span className="text-[13px] text-white/80 group-hover:text-white truncate">
                            {c.title}
                          </span>
                        </div>
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          className="text-white/30 group-hover:text-white/70 group-hover:translate-x-0.5 transition flex-shrink-0"
                        >
                          <path d="M5 12h14M13 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    );
                  })}
                </div>
              </section>
            </div>

            {/* Drawer footer actions */}
            <div className="flex items-center gap-2 px-7 py-4 border-t border-white/5">
              <button
                onClick={() => setDrawerId(null)}
                className="flex-1 px-4 py-2.5 rounded-lg text-xs text-white/70 hover:text-white/95 transition"
                style={{
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.06)",
                }}
              >
                Pin
              </button>
              <button
                onClick={() => {
                  if (drawerId) {
                    handleDrillDown(drawerId);
                    setDrawerId(null);
                  }
                }}
                className="flex-1 px-4 py-2.5 rounded-lg text-xs font-medium transition"
                style={{
                  background: `linear-gradient(180deg, ${CATEGORY_STYLE[drawerNode.category].fill}30, ${CATEGORY_STYLE[drawerNode.category].fill}15)`,
                  border: `1px solid ${CATEGORY_STYLE[drawerNode.category].fill}40`,
                  color: CATEGORY_STYLE[drawerNode.category].fill,
                }}
              >
                Drill down →
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}