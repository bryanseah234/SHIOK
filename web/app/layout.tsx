import React from "react";
import "maplibre-gl/dist/maplibre-gl.css";

export const metadata = {
  title: "S.H.I.O.K. Index",
  description:
    "An explainable comfort score for source-derived Singapore postal records measuring rain shelter, heat, crossing friction, transit access, and bus frequency.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body
        style={{
          margin: 0,
          fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          backgroundColor: "#0f172a",
          color: "#f8fafc",
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </body>
    </html>
  );
}
