"use client";

import { useMemo, useRef, useState } from "react";
import { Canvas, useFrame, ThreeEvent } from "@react-three/fiber";
import { OrbitControls, Html, Line } from "@react-three/drei";
import * as THREE from "three";

type GNode = { id: string; count: number; theme: string };
type GEdge = { source: string; target: string; weight: number };

const THEME_COLORS: Record<string, string> = {
  "local-first & sovereignty": "#5eead4",
  "architecture & compiler": "#818cf8",
  "models & inference": "#f472b6",
  "agents & orchestration": "#fbbf24",
  "data & annotation": "#34d399",
  "web & deployment": "#60a5fa",
  other: "#64748b",
};

// deterministic 3D layout on a sphere, sized by seeded angle from node id
function layout(nodes: GNode[]): Record<string, THREE.Vector3> {
  const pos: Record<string, THREE.Vector3> = {};
  const n = nodes.length;
  nodes.forEach((node, i) => {
    // fibonacci sphere for even distribution
    const phi = Math.acos(1 - (2 * (i + 0.5)) / n);
    const theta = Math.PI * (1 + Math.sqrt(5)) * i;
    const r = 9 + Math.min(node.count, 200) / 40;
    pos[node.id] = new THREE.Vector3(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.sin(phi) * Math.sin(theta),
      r * Math.cos(phi)
    );
  });
  return pos;
}

function Node({
  node,
  position,
  onHover,
  active,
}: {
  node: GNode;
  position: THREE.Vector3;
  onHover: (n: GNode | null) => void;
  active: boolean;
}) {
  const ref = useRef<THREE.Mesh>(null);
  const color = THEME_COLORS[node.theme] || "#64748b";
  const size = 0.28 + Math.min(node.count, 220) / 420;
  useFrame((state) => {
    if (ref.current) {
      const s = active ? 1.5 : 1;
      ref.current.scale.lerp(new THREE.Vector3(s, s, s), 0.2);
    }
  });
  return (
    <group position={position}>
      <mesh
        ref={ref}
        onPointerOver={(e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          onHover(node);
        }}
        onPointerOut={() => onHover(null)}
      >
        <sphereGeometry args={[size, 24, 24]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={active ? 1.1 : 0.4}
          roughness={0.3}
          metalness={0.1}
        />
      </mesh>
      {active && (
        <Html center distanceFactor={22} style={{ pointerEvents: "none" }}>
          <div className="mono" style={{
            background: "rgba(5,6,10,0.92)",
            border: "1px solid #5eead4",
            borderRadius: 8,
            padding: "6px 10px",
            whiteSpace: "nowrap",
            fontSize: 12,
            color: "#e6edf3",
          }}>
            {node.id} · {node.count}
          </div>
        </Html>
      )}
    </group>
  );
}

function Graph({ nodes, edges }: { nodes: GNode[]; edges: GEdge[] }) {
  const positions = useMemo(() => layout(nodes), [nodes]);
  const [hover, setHover] = useState<GNode | null>(null);
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    if (groupRef.current && !hover) {
      groupRef.current.rotation.y += delta * 0.08;
    }
  });

  return (
    <group ref={groupRef}>
      {edges.map((e, i) => {
        const a = positions[e.source];
        const b = positions[e.target];
        if (!a || !b) return null;
        const on = hover && (hover.id === e.source || hover.id === e.target);
        return (
          <Line
            key={i}
            points={[a, b]}
            color={on ? "#5eead4" : "#243044"}
            lineWidth={on ? 2 : 1}
            transparent
            opacity={on ? 0.9 : 0.28}
          />
        );
      })}
      {nodes.map((n) => (
        <Node
          key={n.id}
          node={n}
          position={positions[n.id]}
          onHover={setHover}
          active={hover?.id === n.id}
        />
      ))}
    </group>
  );
}

export default function KnowledgeGraph3D({
  nodes,
  edges,
}: {
  nodes: GNode[];
  edges: GEdge[];
}) {
  return (
    <div style={{ width: "100%", height: "100%" }}>
      <Canvas camera={{ position: [0, 0, 26], fov: 55 }} dpr={[1, 2]}>
        <color attach="background" args={["#05060a"]} />
        <ambientLight intensity={0.6} />
        <pointLight position={[20, 20, 20]} intensity={1.2} />
        <pointLight position={[-20, -10, -20]} intensity={0.6} color="#818cf8" />
        <Graph nodes={nodes} edges={edges} />
        <OrbitControls
          enablePan={false}
          enableZoom={true}
          minDistance={12}
          maxDistance={44}
          autoRotate={false}
        />
      </Canvas>
    </div>
  );
}
