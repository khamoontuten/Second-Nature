import { useEffect, useState } from "react";

/** Value that follows `value` after it has been stable for `ms` (keeps slider-driven calculator calls in check). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}
