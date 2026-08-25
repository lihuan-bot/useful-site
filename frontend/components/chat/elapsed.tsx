"use client";

/** 距 since（ms 时间戳）经过的秒数，每秒刷新——工具执行 / 模型思考的耗时展示 */

import { useEffect, useState } from "react";

export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return <span>{seconds}s</span>;
}
