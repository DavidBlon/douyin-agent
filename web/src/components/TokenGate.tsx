import { useState } from "react";
import { KeyRound } from "lucide-react";
import { setToken } from "../api";

/** 后端令牌校验失败（或还没填过令牌）时的全屏输入卡片。 */
export function TokenGate() {
  const [value, setValue] = useState("");

  const save = () => {
    const token = value.trim();
    if (!token) return;
    setToken(token);
    window.location.reload();
  };

  return (
    <div className="boot-screen">
      <div className="boot-card">
        <div className="brand-mark logo large"><img src="/logo.png" alt="抖音智能体" /></div>
        <h1>
          <KeyRound size={16} style={{ verticalAlign: -2, marginRight: 6 }} />
          需要本地访问令牌
        </h1>
        <p>
          本地 API 已开启令牌鉴权。令牌保存在运行服务的机器上：
          <code>data/local_token</code> 文件，或启动日志
          <code>data/logs/server.log</code> 里「本地 API 令牌」那一行。
          也可以在 <code>.env</code> 里用 <code>LOCAL_API_TOKEN</code> 指定。
        </p>
        <form
          className="token-gate-form"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <input
            className="input"
            autoFocus
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="粘贴访问令牌"
            spellCheck={false}
          />
          <button className="btn btn-primary" type="submit" disabled={!value.trim()}>
            保存并重连
          </button>
        </form>
      </div>
    </div>
  );
}
