import { useMemo } from "react";
import { useTheme } from "../lib/theme";
import { readTokens, type Tokens } from "./options";

/** Chart tokens re-read whenever the resolved theme changes (dark mode is selected values, not a flip). */
export function useTokens(): Tokens {
  const { resolved } = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readTokens(), [resolved]);
}
