"use client";

import { useEffect, useRef } from "react";

export default function BackgroundOrbs() {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const animationId = useRef<number>(0);
    const t = useRef<number>(0);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        let W = window.innerWidth;
        let H = window.innerHeight;

        const resize = () => {
            const dpr = window.devicePixelRatio || 1;
            W = window.innerWidth;
            H = window.innerHeight;
            canvas.width = W * dpr;
            canvas.height = H * dpr;
            canvas.style.width = W + "px";
            canvas.style.height = H + "px";
            ctx.scale(dpr, dpr);
        };

        resize();
        window.addEventListener("resize", resize);

        // Number of horizontal lines making up the mesh
        const linesCount = 50;
        const waveAmplitude = 160;

        const draw = () => {
            ctx.clearRect(0, 0, W, H);

            t.current += 0.003; // animation speed

            for (let i = 0; i < linesCount; i++) {
                ctx.beginPath();

                // Depth normal (0 = back, 1 = front)
                const z = i / (linesCount - 1);

                // Front lines are slightly darker and thicker for perspective depth
                const alpha = 0.02 + z * 0.08;
                ctx.strokeStyle = `rgba(0, 0, 0, ${alpha})`;
                ctx.lineWidth = 0.5 + z * 0.8;

                let started = false;

                for (let px = -50; px <= W + 50; px += 15) {
                    const nx = px / W; // normalized x (0 to 1)

                    // Math for organic flowing landscape (terrain height generation)
                    const pX = nx * 6; // frequency along width
                    const pZ = z * 6;  // frequency along depth

                    let y = 0;
                    y += Math.sin(pX - t.current + pZ) * 1.0;
                    y += Math.cos(pX * 1.3 + t.current * 0.8 - pZ * 0.7) * 0.5;
                    y += Math.sin(pX * 2.1 - t.current * 1.2 + pZ * 1.5) * 0.25;

                    // Smooth bell curve envelope so edges flatten out organically
                    const envelopeX = Math.exp(-Math.pow((nx - 0.5) * 3, 2));

                    // Final elevation
                    const elevation = y * waveAmplitude * envelopeX;

                    // Perspective projection trick: vertical position is depth based
                    // Shifted slightly down so the wave flows nicely under the card
                    const baseV = H * 0.35 + (z * H * 0.45);

                    const finalY = baseV - elevation;

                    if (!started) {
                        ctx.moveTo(px, finalY);
                        started = true;
                    } else {
                        ctx.lineTo(px, finalY);
                    }
                }

                ctx.stroke();
            }

            animationId.current = requestAnimationFrame(draw);
        };

        draw();

        return () => {
            cancelAnimationFrame(animationId.current);
            window.removeEventListener("resize", resize);
        };
    }, []);

    return (
        <canvas
            ref={canvasRef}
            aria-hidden="true"
            style={{
                position: "fixed",
                inset: 0,
                zIndex: 0,
                pointerEvents: "none",
            }}
        />
    );
}
