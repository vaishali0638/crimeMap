import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// eslint-config-next includes react, react-hooks, import and jsx-a11y (accessibility) rules.
const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts", "public/maplibre/**"] },
  {
    rules: {
      "no-console": ["error", { allow: ["warn", "error"] }],
      "react/no-danger": "error",
    },
  },
];

export default config;
