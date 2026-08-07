# desk/ui.py

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A2A Trade Terminal</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root { --accent: #10b981; --panel: #0a0a0a; --panel-2: #050505; --border: #1e293b; }
  body { background: #000; font-family: ui-monospace, monospace; }
  .glow { box-shadow: 0 0 18px rgba(16,185,129,0.1); }
  .panel { background: var(--panel); border: 1px solid var(--border); }
  .panel-2 { background: var(--panel-2); border: 1px solid var(--border); }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }

  @keyframes ticker-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
  .ticker-track { animation: ticker-scroll 30s linear infinite; }

  @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .live-dot { animation: pulse-dot 1.4s ease-in-out infinite; }
  .fade-in { animation: fadeIn .25s ease-out; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px);} to { opacity: 1; transform: translateY(0);} }
  .chat-msg-user { background: #111827; text-align: right; border-left: 2px solid #3b82f6; padding: 8px; }
  .chat-msg-ai { background: #050505; border-left: 2px solid var(--accent); white-space: pre-wrap; padding: 8px; }

  .search-results { position: absolute; top: 100%; left: 0; right: 0; background: #050505; border: 1px solid var(--border); z-index: 50; max-height: 150px; overflow-y: auto; }
  .search-item { padding: 4px 8px; cursor: pointer; display: flex; justify-content: space-between; font-size: 10px; }
  .search-item:hover { background: #1e293b; }

  .badge { padding: 2px 6px; border-radius: 2px; font-weight: bold; font-size: 10px; }
  .bg-buy { background: #10b981; color: black; }
  .bg-sell { background: #ef4444; color: white; }
  .bg-hold, .bg-n\/a { background: #6b7280; color: white; }

  .tf-btn { padding: 2px 8px; border: 1px solid var(--border); font-size: 10px; cursor: pointer; background: transparent; color: #9ca3af; }
  .tf-btn.active { background: #10b981; color: black; font-weight: bold; border-color: #10b981; }

  .ind-btn { padding: 2px 7px; border: 1px solid var(--border); font-size: 9px; cursor: pointer; background: transparent; color: #9ca3af; white-space: nowrap; }
  .ind-btn.active { background: #1e293b; color: #34d399; border-color: #10b981; font-weight: bold; }

  /* Agent Trace */
  .trace-box { margin-top: 8px; border: 1px solid var(--border); background: #000; white-space: normal; }
  .trace-box > summary { cursor: pointer; padding: 4px 8px; font-size: 9px; letter-spacing: .08em; text-transform: uppercase; color: #64748b; list-style: none; user-select: none; }
  .trace-box > summary:hover { color: #34d399; }
  .trace-box[open] > summary { border-bottom: 1px solid var(--border); color: #34d399; }
  .trace-step { padding: 5px 8px; border-bottom: 1px dashed #111827; font-size: 9px; }
  .trace-step:last-child { border-bottom: none; }
  .trace-head { display: flex; gap: 6px; align-items: center; margin-bottom: 2px; }
  .trace-agent { color: #a78bfa; font-weight: bold; }
  .trace-tool { color: #22d3ee; }
  .trace-ts { color: #374151; margin-left: auto; }
  .trace-io { color: #6b7280; line-height: 1.35; word-break: break-word; }
  .trace-label { display: inline-block; min-width: 22px; color: #374151; }
  .trace-rail { border-left: 2px solid #1e293b; }

  /* Human-in-the-loop card */
  .hitl-card { margin-top: 8px; border: 1px solid #78350f; background: #1c1207; padding: 10px; white-space: normal; }
  .hitl-title { color: #fbbf24; font-weight: bold; font-size: 10px; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 4px; }
  .hitl-detail { color: #d1d5db; font-size: 10px; margin-bottom: 8px; }
  .hitl-btn { padding: 4px 14px; font-size: 10px; font-weight: bold; cursor: pointer; border: none; }
  .hitl-approve { background: #10b981; color: #000; }
  .hitl-approve:hover { background: #34d399; }
  .hitl-reject { background: #ef4444; color: #fff; }
  .hitl-reject:hover { background: #f87171; }
  .hitl-btn:disabled { opacity: .4; cursor: not-allowed; }

  /* Optimizer leaderboard */
  .opt-table { width: 100%; border-collapse: collapse; font-size: 9px; }
  .opt-table th { color: #64748b; text-align: right; font-weight: normal; padding: 2px 4px; border-bottom: 1px solid var(--border); }
  .opt-table th:first-child { text-align: left; }
  .opt-table td { padding: 2px 4px; text-align: right; border-bottom: 1px solid #0f172a; }
  .opt-table td:first-child { text-align: left; color: #cbd5e1; }
  .opt-table tr.opt-best td { background: #052e21; color: #34d399; font-weight: bold; }
  .opt-table tr.opt-default td { background: #1e1b0a; }

  /* Cost + latency meters */
  .meter-row { display: flex; gap: 10px; flex-wrap: wrap; font-size: 9px; color: #475569; padding: 3px 8px; border-bottom: 1px solid var(--border); }
  .meter-row b { color: #94a3b8; font-weight: 600; }

  /* Injection block chip */
  .inj-chip { display: inline-block; background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; padding: 1px 5px; font-size: 8px; letter-spacing: .05em; }
  .news-blocked { opacity: .55; border-color: #7f1d1d !important; }

  /* Confidence badge */
  .conf-HIGH { background: #10b981; color: #000; }
  .conf-MEDIUM { background: #eab308; color: #000; }
  .conf-LOW { background: #ef4444; color: #fff; }
  .conf-NONE { background: #6b7280; color: #fff; }

  /* Baseline comparison */
  .cmp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
  .cmp-col { border: 1px solid var(--border); padding: 8px; background: #000; white-space: pre-wrap; font-size: 10px; }
  .cmp-col h4 { font-size: 9px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 4px; }
  .cmp-agentic h4 { color: #34d399; }
  .cmp-baseline h4 { color: #f59e0b; }

  #toast-container { position: fixed; bottom: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; }
  .toast { background: #050505; border-left: 4px solid var(--accent); padding: 12px 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.8); color: #d1d5db; min-width: 280px; font-family: ui-monospace, monospace; animation: slideIn 0.3s ease-out forwards; }
  @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

  /* ---------------------------------------------------------------- Lock screen */
  #login-screen {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    background: #05070a;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    transition: opacity .45s ease, transform .45s ease;
  }
  /* Faint terminal grid + a slow vertical sweep, so the panel sits on a surface
     instead of floating on flat colour. */
  #login-screen::before {
    content: "";
    position: absolute; inset: 0;
    background-image:
      linear-gradient(rgba(0,255,157,.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,157,.035) 1px, transparent 1px);
    background-size: 48px 48px;
    mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, #000 20%, transparent 100%);
    -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, #000 20%, transparent 100%);
    pointer-events: none;
  }
  #login-screen::after {
    content: "";
    position: absolute; left: 0; right: 0; height: 140px;
    background: linear-gradient(180deg, transparent, rgba(0,255,157,.045), transparent);
    animation: lockSweep 7s linear infinite;
    pointer-events: none;
  }
  @keyframes lockSweep { 0% { top: -140px; } 100% { top: 100%; } }

  .lock-wordmark {
    position: relative;
    text-align: center;
    margin-bottom: 34px;
    animation: lockRise .6s cubic-bezier(.2,.8,.2,1) both;
  }
  .lock-wordmark h1 {
    font-size: 30px;
    font-weight: 600;
    letter-spacing: .30em;
    color: #e8fff5;
    margin: 0 0 0 .30em;   /* offsets the trailing letter-space so it reads centred */
    text-shadow: 0 0 26px rgba(0,255,157,.28);
  }
  .lock-wordmark .rule {
    width: 46px; height: 1px; margin: 16px auto 0;
    background: linear-gradient(90deg, transparent, #00ff9d, transparent);
  }
  .lock-wordmark p {
    margin: 14px 0 0;
    font-size: 9px;
    letter-spacing: .34em;
    text-transform: uppercase;
    color: #4b6b60;
  }

  .login-box {
    position: relative;
    width: 330px;
    padding: 30px 30px 26px;
    background: rgba(9,14,18,.86);
    border: 1px solid rgba(0,255,157,.16);
    border-radius: 4px;
    box-shadow: 0 24px 70px rgba(0,0,0,.75), inset 0 1px 0 rgba(255,255,255,.03);
    backdrop-filter: blur(6px);
    animation: lockRise .6s cubic-bezier(.2,.8,.2,1) .08s both;
  }
  @keyframes lockRise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }

  .lock-field { position: relative; margin-bottom: 14px; }
  .lock-field label {
    display: block;
    font-size: 8px;
    letter-spacing: .22em;
    text-transform: uppercase;
    color: #5c7a70;
    margin-bottom: 6px;
  }
  .login-box input {
    width: 100%;
    padding: 11px 12px;
    background: #04070a;
    border: 1px solid #16241f;
    border-radius: 3px;
    color: #d7fff0;
    font-family: inherit;
    font-size: 13px;
    letter-spacing: .06em;
    outline: none;
    box-sizing: border-box;
    transition: border-color .18s ease, box-shadow .18s ease;
  }
  .login-box input::placeholder { color: #2f423b; letter-spacing: .1em; }
  .login-box input:focus {
    border-color: #00ff9d;
    box-shadow: 0 0 0 1px rgba(0,255,157,.18), 0 0 18px rgba(0,255,157,.10);
  }
  .login-box button {
    width: 100%;
    margin-top: 8px;
    padding: 11px;
    background: #00ff9d;
    color: #04070a;
    font-family: inherit;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .22em;
    text-transform: uppercase;
    border: none;
    border-radius: 3px;
    cursor: pointer;
    transition: background .16s ease, transform .08s ease, box-shadow .16s ease;
  }
  .login-box button:hover { background: #4dffbb; box-shadow: 0 0 24px rgba(0,255,157,.30); }
  .login-box button:active { transform: translateY(1px); }

  .lock-foot {
    display: flex; align-items: center; justify-content: center; gap: 7px;
    margin-top: 18px;
    font-size: 8px; letter-spacing: .18em; text-transform: uppercase; color: #38514a;
  }
  .lock-foot i {
    width: 5px; height: 5px; border-radius: 50%;
    background: #00ff9d; box-shadow: 0 0 8px #00ff9d;
    animation: lockPulse 2.2s ease-in-out infinite; font-style: normal;
  }
  @keyframes lockPulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }

  .error-msg {
    display: none;
    margin-top: 14px;
    padding: 7px 9px;
    background: rgba(255,68,68,.07);
    border-left: 2px solid #ff4444;
    color: #ff8080;
    font-size: 10px;
    letter-spacing: .08em;
  }
  .login-box.denied { animation: lockShake .32s ease; border-color: rgba(255,68,68,.4); }
  @keyframes lockShake {
    0%,100% { transform: translateX(0); }
    20% { transform: translateX(-7px); } 40% { transform: translateX(6px); }
    60% { transform: translateX(-4px); } 80% { transform: translateX(3px); }
  }
</style>
</head>
<body class="text-gray-300 h-screen flex flex-col overflow-hidden text-xs">

  <!-- ------------------------------------------------------------ Lock screen -->
  <div id="login-screen">
      <div class="lock-wordmark">
          <h1>A2A TRADE TERMINAL</h1>
          <div class="rule"></div>
          <p>Multi-Agent Trading Desk</p>
      </div>

      <div class="login-box" id="login-box">
          <div class="lock-field">
              <label for="username">Operator</label>
              <input type="text" id="username" placeholder="username" autocomplete="off"
                     onkeypress="if(event.key === 'Enter') document.getElementById('password').focus()">
          </div>
          <div class="lock-field">
              <label for="password">Passphrase</label>
              <input type="password" id="password" placeholder="••••••••"
                     onkeypress="if(event.key === 'Enter') attemptLogin()">
          </div>

          <button onclick="attemptLogin()">Authorize</button>
          <div id="login-error" class="error-msg">Access denied — invalid credentials.</div>

          <div class="lock-foot"><i></i> Session encrypted</div>
      </div>
  </div>

  <div id="toast-container"></div>

  <header class="panel-2 border-b border-[var(--border)] px-4 py-2 flex items-center justify-between shrink-0">
    <div class="flex items-center gap-3">
      <div class="w-2.5 h-2.5 rounded-full bg-emerald-500 live-dot"></div>
      <h1 class="text-sm font-bold tracking-widest text-emerald-400">A2A TRADE TERMINAL</h1>
      <span class="text-[10px] text-gray-500 border border-[var(--border)] rounded px-2">PAPER TRADING LIVE</span>
      <span class="text-[10px] text-violet-400 border border-violet-900 rounded px-2">MULTI-AGENT A2A</span>
    </div>
    <div class="flex items-center gap-2 text-[10px]">
      <span class="text-gray-600 uppercase tracking-wider">Market</span>
      <select id="simScenario" onchange="setScenario(this.value)"
              class="bg-black border border-[var(--border)] text-gray-300 px-1 py-0.5 text-[10px] focus:outline-none focus:border-emerald-400">
        <option value="live">Live feed</option>
        <option value="normal">Sim: normal</option>
        <option value="rally">Sim: rally</option>
        <option value="crash">Sim: crash</option>
        <option value="choppy">Sim: choppy</option>
      </select>
      <label class="flex items-center gap-1 text-gray-500 cursor-pointer" title="Run the same question through a single tool-less model for comparison">
        <input type="checkbox" id="compareBaseline" class="accent-emerald-500"> vs baseline
      </label>
      <label class="flex items-center gap-1 text-gray-500 cursor-pointer" title="Inject a hostile headline into the news feed to demonstrate the prompt-injection filter">
        <input type="checkbox" id="injectDemo" class="accent-red-500"> injection test
      </label>
      
      <!-- BOTÓN DE GUÍA AI & REGULATIONS -->
      <button onclick="requestAIGuide()" class="text-emerald-400 hover:text-emerald-300 border border-emerald-900 bg-emerald-900/20 px-3 py-0.5 font-bold">⚖️ AI GUIDE</button>
      
      <button onclick="openRegulationPanel()" class="text-gray-600 hover:text-orange-400 border border-[var(--border)] px-2 py-0.5">REGULATIONS</button>
      <a href="/api/audit/tail" target="_blank" class="text-gray-600 hover:text-emerald-400 border border-[var(--border)] px-2 py-0.5">AUDIT</a>
    </div>
  </header>

  <div class="panel-2 border-b border-[var(--border)] overflow-hidden whitespace-nowrap py-1 shrink-0">
    <div id="tickerTrack" class="ticker-track inline-flex gap-10 px-4 text-xs"></div>
  </div>

  <main class="flex-1 flex gap-2 p-2 overflow-hidden">
    <!-- LEFT SIDEBAR -->
    <div class="w-64 flex flex-col gap-2 shrink-0">
      <section class="panel glow flex flex-col h-1/2 overflow-hidden p-3">
        <h2 class="uppercase tracking-widest text-emerald-400 font-bold mb-2">Watchlist</h2>
        <div class="relative mb-2 shrink-0">
          <input id="searchInput" type="text" placeholder="Search symbol..." autocomplete="off"
                 class="w-full bg-black border border-[var(--border)] px-2 py-1 text-white focus:outline-none focus:border-emerald-400">
          <div id="searchResults" class="search-results hidden"></div>
        </div>
        <div id="symbolList" class="flex-1 overflow-y-auto space-y-1"></div>
      </section>

      <section class="panel flex flex-col h-1/2 overflow-hidden p-3 border-t-2 border-emerald-900">
        <h2 class="uppercase tracking-widest text-emerald-400 font-bold mb-2 flex justify-between">
          Portfolio <button onclick="fetchPortfolio()" class="text-gray-500 hover:text-white">↻</button>
        </h2>
        <div class="mb-2 p-2 bg-black border border-[var(--border)]">
          <div class="flex justify-between text-[10px] text-gray-400">Equity <span id="portEquity" class="text-white font-bold">$0.00</span></div>
          <div class="flex justify-between text-[10px] text-gray-400 mt-1">Buying Power <span id="portBp" class="text-emerald-300">$0.00</span></div>
        </div>
        <div class="text-[10px] text-gray-500 mb-1 border-b border-[var(--border)] pb-1">POSITIONS & PENDING ORDERS</div>
        <div id="positionsList" class="flex-1 overflow-y-auto space-y-1"></div>
      </section>
    </div>

    <!-- MAIN CONTENT AREA -->
    <div class="flex-1 flex flex-col gap-2 overflow-hidden">
      <!-- TOP: Chart & Controls -->
      <section class="panel glow p-2 flex flex-col h-[55%] overflow-hidden relative">
        <div id="chartError" class="absolute inset-0 z-10 flex items-center justify-center bg-black/80 hidden">
            <div class="text-center">
                <div class="text-red-400 font-bold mb-2 uppercase tracking-widest">Market Data Unavailable</div>
                <div class="text-gray-500 text-[10px]">No recent data returned from feed.</div>
            </div>
        </div>

        <div class="flex items-center justify-between mb-1 shrink-0 bg-black p-2 border border-[var(--border)] relative z-20">
          <div class="flex items-center gap-4">
            <div>
              <span id="activeSymbol" class="text-xl font-bold text-white">AAPL</span>
              <span id="activePrice" class="text-sm text-emerald-300 tabular-nums ml-2">—</span>
              <span id="chartLiveStatus" class="text-[9px] text-gray-500 ml-2">EOD</span>
            </div>
            <div id="aiStrip" class="hidden flex items-center gap-2 border-l border-gray-700 pl-4">
              <span class="text-[10px] text-gray-500 uppercase">AI Signal:</span>
              <span id="aiBadge" class="badge">...</span>
              <span id="aiRationale" class="text-[10px] text-gray-400 italic max-w-xs truncate"></span>
            </div>
          </div>

          <div class="flex gap-1">
            <button class="tf-btn" data-tf="5Min">5m</button>
            <button class="tf-btn" data-tf="15Min">15m</button>
            <button class="tf-btn" data-tf="1Hour">1h</button>
            <button class="tf-btn active" data-tf="1Day">1D</button>
            <button class="tf-btn" data-tf="1Week">1W</button>
          </div>

          <!-- CONTROLES DE TRADING Y MENÚ DESPLEGABLE DE HERRAMIENTAS IA -->
          <div class="flex items-center gap-2 border-l border-gray-700 pl-4 relative" id="aiToolsDropdownContainer">
            <span class="text-[10px] text-gray-500 uppercase">Qty</span>
            <input id="tradeQty" type="number" min="1" value="1" class="w-14 bg-black border border-[var(--border)] text-white text-center py-1 text-xs focus:outline-none focus:border-emerald-400">
            
            <button onclick="executeOrder('buy')" class="bg-emerald-600 hover:bg-emerald-500 text-black font-bold px-4 py-1 text-xs">BUY</button>
            <button onclick="executeOrder('sell')" class="bg-red-600 hover:bg-red-500 text-white font-bold px-4 py-1 text-xs">SELL</button>
            
            <!-- Botón del Dropdown -->
            <button onclick="toggleToolsMenu()" class="bg-gray-800 hover:bg-gray-700 border border-gray-600 text-white font-bold px-3 py-1 text-xs ml-2 flex items-center gap-1 transition-colors">
              🛠️ AI TOOLS 
              <span class="text-[9px]">▼</span>
            </button>
            
            <!-- Contenido del Menú Desplegable -->
            <div id="aiToolsMenu" class="hidden absolute top-full right-0 mt-2 w-40 bg-[#0a0e17] border border-[var(--border)] shadow-2xl z-50 flex flex-col rounded-sm overflow-hidden">
              <button id="strategyBtn" onclick="runStrategy(); toggleToolsMenu();" class="text-left w-full hover:bg-blue-600 hover:text-white text-blue-400 font-bold px-3 py-2.5 text-xs border-b border-[#1f2937] transition-colors">📊 STRATEGY</button>
              <button id="advisorBtn" onclick="runAdvisor(); toggleToolsMenu();" class="text-left w-full hover:bg-cyan-600 hover:text-white text-cyan-400 font-bold px-3 py-2.5 text-xs border-b border-[#1f2937] transition-colors">🤖 AI ADVISOR</button>
              <button id="challengeBtn" onclick="challengeStrategy(); toggleToolsMenu();" class="text-left w-full hover:bg-rose-700 hover:text-white text-rose-400 font-bold px-3 py-2.5 text-xs border-b border-[#1f2937] transition-colors">⚔️ CHALLENGE</button>
              <button id="metricsBtn" onclick="openPredictionMetrics(); toggleToolsMenu();" class="text-left w-full hover:bg-slate-700 hover:text-white text-slate-300 font-bold px-3 py-2.5 text-xs border-b border-[#1f2937] transition-colors">📈 AI METRICS</button>
              <button id="backtestBtn" onclick="runBacktest(); toggleToolsMenu();" class="text-left w-full hover:bg-violet-600 hover:text-white text-violet-400 font-bold px-3 py-2.5 text-xs border-b border-[#1f2937] transition-colors">⏱ BACKTEST</button>
              <button id="optimizeBtn" onclick="runOptimizer(); toggleToolsMenu();" class="text-left w-full hover:bg-amber-600 hover:text-white text-amber-400 font-bold px-3 py-2.5 text-xs transition-colors">⚙ OPTIMIZE</button>
            </div>
          </div>
        </div>

        <div id="strategyPanel" class="hidden absolute top-14 right-4 z-30 w-72 panel border border-blue-800 p-3 glow"></div>
        <div id="advisorPanel" class="hidden absolute top-14 right-4 z-30 w-[28rem] max-h-[calc(100%-4rem)] overflow-y-auto panel border border-cyan-800 p-3 glow"></div>
        <div id="riskPanel" class="hidden absolute top-14 right-4 z-30 w-80 panel border border-slate-600 p-3 glow"></div>
        <div id="backtestPanel" class="hidden absolute top-14 right-4 z-30 w-80 panel border border-violet-800 p-3 glow"></div>
        <div id="optimizerPanel" class="hidden absolute top-14 right-4 z-30 w-[26rem] panel border border-amber-700 p-3 glow"></div>
        <div id="regulationPanel" class="hidden absolute top-14 right-4 z-30 w-[30rem] max-h-[calc(100%-4rem)] overflow-y-auto panel border border-orange-800 p-3 glow"></div>

        <div class="flex flex-wrap items-center gap-1 mb-1 shrink-0 bg-black p-1.5 border border-[var(--border)] relative z-20">
          <span class="text-[9px] text-gray-500 uppercase mr-1">Indicators:</span>
          <button class="ind-btn" data-ind="sma20">SMA 20</button>
          <button class="ind-btn" data-ind="sma50">SMA 50</button>
          <button class="ind-btn" data-ind="ema12">EMA 12</button>
          <button class="ind-btn" data-ind="bb">Bollinger 20/2</button>
          <button class="ind-btn" data-ind="vwap">VWAP</button>
          <button class="ind-btn" data-ind="volume">Volume</button>
          <button class="ind-btn" data-ind="rsi">RSI 14</button>
          <button class="ind-btn" data-ind="macd">MACD</button>
          <button class="ind-btn" data-ind="stoch">Stochastic</button>
          <button class="ind-btn" data-ind="atr">ATR 14</button>
          <button class="ind-btn" data-ind="fib">Fibonacci</button>
          <button class="ind-btn" data-ind="sr">Support/Resistance</button>
          <button class="ind-btn" data-ind="log">Log Scale</button>
        </div>

        <div id="chart" class="flex-1 min-h-0 relative z-0"></div>
        <div id="rsiPanel" class="hidden h-[70px] shrink-0 border-t border-[var(--border)] mt-1 pt-1">
          <div class="flex justify-between text-[9px] text-gray-500 px-1"><span>RSI (14)</span><span id="rsiValue">—</span></div>
          <div id="rsiChart" class="w-full h-[52px]"></div>
        </div>
        <div id="macdPanel" class="hidden h-[70px] shrink-0 border-t border-[var(--border)] mt-1 pt-1">
          <div class="flex justify-between text-[9px] text-gray-500 px-1"><span>MACD (12, 26, 9)</span><span id="macdValue">—</span></div>
          <div id="macdChart" class="w-full h-[52px]"></div>
        </div>
        <div id="stochPanel" class="hidden h-[70px] shrink-0 border-t border-[var(--border)] mt-1 pt-1">
          <div class="flex justify-between text-[9px] text-gray-500 px-1"><span>Stochastic (14, 3)</span><span id="stochValue">—</span></div>
          <div id="stochChart" class="w-full h-[52px]"></div>
        </div>
        <div id="atrPanel" class="hidden h-[70px] shrink-0 border-t border-[var(--border)] mt-1 pt-1">
          <div class="flex justify-between text-[9px] text-gray-500 px-1"><span>ATR (14)</span><span id="atrValue">—</span></div>
          <div id="atrChart" class="w-full h-[52px]"></div>
        </div>
      </section>

      <!-- BOTTOM: News and Agent Chat Split -->
      <div class="flex-1 flex gap-2 overflow-hidden">
        <section class="panel w-1/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold">News Stream</h2>
          <div id="newsList" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
        </section>

        <section class="panel w-2/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold flex gap-2 items-center">
            Portfolio Manager Desk
            <span class="bg-violet-900 text-violet-300 px-1 rounded text-[9px]">A2A · ANALYST + OPTIMIZER + CRO</span>
            <span class="bg-yellow-900 text-yellow-300 px-1 rounded text-[9px]">HITL GUARDRAIL</span>
          </h2>
          <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1"></div>
          <form id="chatForm" class="flex gap-1 shrink-0">
            <input id="chatInput" type="text" placeholder="e.g. 'Have the analyst and the CRO assess AAPL, and if they approve buy 2 shares'..."
                   class="flex-1 bg-black border border-[var(--border)] px-2 py-1.5 focus:outline-none focus:border-emerald-400 text-white" />
            <button class="bg-emerald-600 hover:bg-emerald-500 text-black font-bold px-4">SEND</button>
          </form>
        </section>
      </div>
    </div>
  </main>

<script>
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(title, message, type='success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const borderColor = type === 'success' ? '#10b981' : (type === 'error' ? '#ef4444' : '#3b82f6');
  toast.className = 'toast border border-[var(--border)]';
  toast.style.borderLeftColor = borderColor;
  toast.innerHTML = `<div class="font-bold text-white text-xs mb-1 uppercase tracking-wider">${esc(title)}</div><div class="text-[10px] text-gray-400">${esc(message)}</div>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}

// ------------------------------------------------------------------
// LOGIN LOGIC
// ------------------------------------------------------------------
function attemptLogin() {
    const user = document.getElementById("username").value.trim();
    const pass = document.getElementById("password").value;
    const errorMsg = document.getElementById("login-error");

    if (user === "John" && pass === "tcsadmin123") {
        const screen = document.getElementById("login-screen");
        screen.style.opacity = "0";
        screen.style.transform = "scale(1.02)";
        setTimeout(() => { screen.style.display = "none"; }, 450);
    } else {
        const box = document.getElementById("login-box");
        errorMsg.style.display = "block";
        box.classList.remove("denied");
        void box.offsetWidth;            // restart the shake animation
        box.classList.add("denied");
        document.getElementById("password").value = "";
        document.getElementById("password").focus();
    }
}

window.addEventListener("load", () => {
    const u = document.getElementById("username");
    if (u) u.focus();
});

// ------------------------------------------------------------------
// DROPDOWN MENU LOGIC
// ------------------------------------------------------------------
function toggleToolsMenu() {
    const menu = document.getElementById('aiToolsMenu');
    menu.classList.toggle('hidden');
}

document.addEventListener('click', (event) => {
    const container = document.getElementById('aiToolsDropdownContainer');
    const menu = document.getElementById('aiToolsMenu');
    if (container && menu && !menu.classList.contains('hidden') && !container.contains(event.target)) {
        menu.classList.add('hidden');
    }
});

// ------------------------------------------------------------------
// AI GUIDE & REGULATIONS BUTTON
// ------------------------------------------------------------------
function requestAIGuide() {
    const chatInput = document.getElementById("chatInput");
    if (chatInput) {
        chatInput.value = "Please provide a quick, highly professional guide on how to use this algorithmic trading terminal. Also, explicitly list the SEC and FINRA financial regulations and risk compliance rules that this agentic system adheres to (like strict stop-loss enforcement and Human-In-The-Loop approvals for high volumes). Use bullet points.";
        document.getElementById("chatForm").dispatchEvent(new Event("submit", { cancelable: true }));
    }
}

let watchlist = JSON.parse(localStorage.getItem('tcs_wl')) || ["AAPL", "MSFT", "TSLA"];
let activeSymbol = watchlist[0];
let activeTimeframe = "1Day";

let chart, candleSeries;
let extraSeries = [];
let strategyLines = [];

let indicatorState = { sma20:false, sma50:false, ema12:false, bb:false, vwap:false, volume:false, rsi:false, macd:false, stoch:false, atr:false, fib:false, sr:false, log:false };
let indicatorSeries = {};
let volumeSeries = null;
let fibLines = [];
let srLines = [];
let lastIndicatorsData = {};
let lastLevelsData = {};
let rsiChart, rsiSeries, macdChart, macdSeries, macdSignalSeries, macdHistogram, stochChart, stochKSeries, stochDSeries, atrChart, atrSeries;

function clearStrategy() {
  strategyLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
  strategyLines = [];
  candleSeries.setMarkers([]);
  document.getElementById('strategyPanel').classList.add('hidden');
  document.getElementById('advisorPanel').classList.add('hidden');
  document.getElementById('riskPanel').classList.add('hidden');
  document.getElementById('backtestPanel').classList.add('hidden');
  const op = document.getElementById('optimizerPanel');
  if (op) op.classList.add('hidden');
}

function initChart() {
  chart = LightweightCharts.createChart(document.getElementById('chart'), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  candleSeries = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });

  chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && rsiChart) { try { rsiChart.timeScale().setVisibleLogicalRange(range); } catch(e) {} }
    [macdChart, stochChart, atrChart].forEach(study => { if (range && study) try { study.timeScale().setVisibleLogicalRange(range); } catch(e) {} });
  });
}

function initRsiChart() {
  rsiChart = LightweightCharts.createChart(document.getElementById('rsiChart'), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: { visible: false, borderVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: false, handleScale: false,
  });
  rsiSeries = rsiChart.addLineSeries({ color: '#a78bfa', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  rsiSeries.createPriceLine({ price: 70, color: '#ef444488', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '70' });
  rsiSeries.createPriceLine({ price: 30, color: '#10b98188', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '30' });
}

function createStudyChart(id) {
  return LightweightCharts.createChart(document.getElementById(id), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: { visible: false, borderVisible: false }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: false, handleScale: false,
  });
}

function initStudyCharts() {
  macdChart = createStudyChart('macdChart');
  macdSeries = macdChart.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  macdSignalSeries = macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  macdHistogram = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
  stochChart = createStudyChart('stochChart');
  stochKSeries = stochChart.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  stochDSeries = stochChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  [80, 20].forEach((price, i) => stochKSeries.createPriceLine({ price, color: i ? '#10b98188' : '#ef444488', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: String(price) }));
  atrChart = createStudyChart('atrChart');
  atrSeries = atrChart.addLineSeries({ color: '#22d3ee', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
}

function clearExtraSeries() { extraSeries.forEach(s => chart.removeSeries(s)); extraSeries = []; }

function addDashedSeries(points, color) {
  const series = chart.addLineSeries({
    color, lineWidth: 2, lineStyle: 2, pointMarkersVisible: true, lastValueVisible: true, priceLineVisible: false,
  });
  series.setData(points); extraSeries.push(series);
}

function ensureVolumeSeries() {
  if (!volumeSeries) {
    volumeSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  }
  return volumeSeries;
}

function toggleLine(key, data, color, on) {
  if (on) {
    if (indicatorSeries[key]) return;
    const s = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true });
    s.setData(data || []);
    indicatorSeries[key] = s;
  } else if (indicatorSeries[key]) {
    chart.removeSeries(indicatorSeries[key]);
    delete indicatorSeries[key];
  }
}

function toggleBollinger(on) {
  if (on) {
    if (indicatorSeries.bb) return;
    const upper = chart.addLineSeries({ color: '#818cf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const mid = chart.addLineSeries({ color: '#818cf880', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
    const lower = chart.addLineSeries({ color: '#818cf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    upper.setData(lastIndicatorsData.bb_upper || []);
    mid.setData(lastIndicatorsData.bb_middle || []);
    lower.setData(lastIndicatorsData.bb_lower || []);
    indicatorSeries.bb = [upper, mid, lower];
  } else if (indicatorSeries.bb) {
    indicatorSeries.bb.forEach(s => chart.removeSeries(s));
    delete indicatorSeries.bb;
  }
}

function toggleVolume(on) {
  if (on) {
    const s = ensureVolumeSeries();
    s.setData(lastIndicatorsData.volume || []);
  } else if (volumeSeries) {
    chart.removeSeries(volumeSeries);
    volumeSeries = null;
  }
}

function toggleRsi(on) {
  const panel = document.getElementById('rsiPanel');
  if (on) {
    panel.classList.remove('hidden');
    rsiSeries.setData(lastIndicatorsData.rsi14 || []);
    const rsiVals = lastIndicatorsData.rsi14 || [];
    document.getElementById('rsiValue').textContent = rsiVals.length ? rsiVals[rsiVals.length - 1].value.toFixed(1) : '—';
    setTimeout(() => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    }, 0);
  } else {
    panel.classList.add('hidden');
  }
}

function syncStudy(chartInstance) {
  setTimeout(() => { const range = chart.timeScale().getVisibleLogicalRange(); if (range) try { chartInstance.timeScale().setVisibleLogicalRange(range); } catch(e) {} }, 0);
}

function toggleMacd(on) {
  document.getElementById('macdPanel').classList.toggle('hidden', !on);
  if (!on) return;
  macdSeries.setData(lastIndicatorsData.macd || []); macdSignalSeries.setData(lastIndicatorsData.macd_signal || []); macdHistogram.setData(lastIndicatorsData.macd_histogram || []);
  const vals = lastIndicatorsData.macd || []; document.getElementById('macdValue').textContent = vals.length ? vals[vals.length - 1].value.toFixed(2) : '—'; syncStudy(macdChart);
}

function toggleStoch(on) {
  document.getElementById('stochPanel').classList.toggle('hidden', !on);
  if (!on) return;
  stochKSeries.setData(lastIndicatorsData.stoch_k || []); stochDSeries.setData(lastIndicatorsData.stoch_d || []);
  const vals = lastIndicatorsData.stoch_k || []; document.getElementById('stochValue').textContent = vals.length ? vals[vals.length - 1].value.toFixed(1) : '—'; syncStudy(stochChart);
}

function toggleAtr(on) {
  document.getElementById('atrPanel').classList.toggle('hidden', !on);
  if (!on) return;
  atrSeries.setData(lastIndicatorsData.atr14 || []);
  const vals = lastIndicatorsData.atr14 || []; document.getElementById('atrValue').textContent = vals.length ? vals[vals.length - 1].value.toFixed(2) : '—'; syncStudy(atrChart);
}

function toggleFib(on) {
  if (on) {
    if (!lastLevelsData.fibonacci) return;
    Object.entries(lastLevelsData.fibonacci).forEach(([level, price]) => {
      fibLines.push(candleSeries.createPriceLine({
        price, color: '#eab308', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true, title: `Fib ${level}`
      }));
    });
  } else {
    fibLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
    fibLines = [];
  }
}

function toggleSr(on) {
  if (on) {
    if (!lastLevelsData.resistance) return;
    srLines.push(candleSeries.createPriceLine({ price: lastLevelsData.resistance, color: '#ef4444', lineWidth: 2, axisLabelVisible: true, title: 'Resistencia' }));
    srLines.push(candleSeries.createPriceLine({ price: lastLevelsData.support, color: '#10b981', lineWidth: 2, axisLabelVisible: true, title: 'Soporte' }));
  } else {
    srLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
    srLines = [];
  }
}

function toggleLog(on) {
  chart.priceScale('right').applyOptions({ mode: on ? LightweightCharts.PriceScaleMode.Logarithmic : LightweightCharts.PriceScaleMode.Normal });
}

function applyIndicator(key, on) {
  if (key === 'sma20') toggleLine('sma20', lastIndicatorsData.sma20, '#60a5fa', on);
  else if (key === 'sma50') toggleLine('sma50', lastIndicatorsData.sma50, '#f59e0b', on);
  else if (key === 'ema12') toggleLine('ema12', lastIndicatorsData.ema12, '#f472b6', on);
  else if (key === 'bb') toggleBollinger(on);
  else if (key === 'vwap') toggleLine('vwap', lastIndicatorsData.vwap, '#22d3ee', on);
  else if (key === 'volume') toggleVolume(on);
  else if (key === 'rsi') toggleRsi(on);
  else if (key === 'macd') toggleMacd(on);
  else if (key === 'stoch') toggleStoch(on);
  else if (key === 'atr') toggleAtr(on);
  else if (key === 'fib') toggleFib(on);
  else if (key === 'sr') toggleSr(on);
  else if (key === 'log') toggleLog(on);
}

function refreshActiveIndicators() {
  Object.keys(indicatorSeries).forEach(key => {
    const val = indicatorSeries[key];
    if (Array.isArray(val)) val.forEach(s => chart.removeSeries(s));
    else chart.removeSeries(val);
  });
  indicatorSeries = {};
  if (volumeSeries) { chart.removeSeries(volumeSeries); volumeSeries = null; }
  fibLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} }); fibLines = [];
  srLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} }); srLines = [];

  Object.entries(indicatorState).forEach(([key, on]) => {
    if (on && key !== 'log') applyIndicator(key, true);
  });
}

document.querySelectorAll('.ind-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.ind;
    indicatorState[key] = !indicatorState[key];
    btn.classList.toggle('active', indicatorState[key]);
    applyIndicator(key, indicatorState[key]);
  });
});

function highlightWatchlist() {
  document.querySelectorAll('.watch-item').forEach(el => {
    if(el.dataset.symbol === activeSymbol) el.classList.add('border-emerald-500', 'text-emerald-300');
    else el.classList.remove('border-emerald-500', 'text-emerald-300');
  });
}

function removeFromWatchlist(sym, event) {
  event.stopPropagation();
  if (watchlist.length <= 1) { showToast('WATCHLIST', 'Cannot remove the last symbol.', 'error'); return; }
  watchlist = watchlist.filter(s => s !== sym);
  localStorage.setItem('tcs_wl', JSON.stringify(watchlist));
  if (activeSymbol === sym) loadSymbol(watchlist[0]);
  else updateWatchlistUI();
}

async function loadSymbol(sym, timeframe = null, silentRefresh = false) {
  activeSymbol = sym;
  if(timeframe) activeTimeframe = timeframe;
  document.getElementById('activeSymbol').textContent = sym;
  if (!silentRefresh) clearStrategy();

  document.querySelectorAll('.tf-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tf === activeTimeframe));
  highlightWatchlist();

  const res = await fetch(`/api/bars/${sym}?timeframe=${activeTimeframe}&limit=150${silentRefresh ? '&refresh=1' : ''}`);
  const data = await res.json();
  const chartErrorDiv = document.getElementById('chartError');

  if(data.bars?.length > 0) {
    chartErrorDiv.classList.add('hidden');
    const seen = new Set();
    const sortedBars = data.bars.filter(b => { if (seen.has(b.time)) return false; seen.add(b.time); return true; })
      .sort((a, b) => typeof a.time === 'number' ? a.time - b.time : String(a.time).localeCompare(String(b.time)));

    candleSeries.setData(sortedBars.map(b => ({time: b.time, open: b.open, high: b.high, low: b.low, close: b.close})));
    const displayPrice = data.quote?.price > 0 ? data.quote.price : sortedBars[sortedBars.length-1].close;
    document.getElementById('activePrice').textContent = displayPrice.toFixed(2);
    const status = document.getElementById('chartLiveStatus');
    status.textContent = data.intraday ? 'INTRADAY • auto 30s' : 'QUOTE • auto 30s';
    status.title = data.intraday ? 'Refreshes every 30 seconds. Provider availability and exchange delay may apply.' : 'The displayed quote refreshes every 30 seconds; daily/weekly candles change only when their source candle updates.';
    chart.timeScale().fitContent();
  } else {
    candleSeries.setData([]); clearExtraSeries();
    document.getElementById('activePrice').textContent = "N/A";
    chartErrorDiv.classList.remove('hidden');
  }

  lastIndicatorsData = data.indicators || {};
  lastLevelsData = data.levels || {};
  refreshActiveIndicators();

  if (!silentRefresh) document.getElementById('aiStrip').classList.remove('hidden');
  document.getElementById('aiBadge').className = 'badge bg-gray-600';
  document.getElementById('aiBadge').textContent = 'ANALYZING...';
  document.getElementById('aiRationale').textContent = '';

  if (!silentRefresh) fetch(`/api/recommend/${sym}?timeframe=${activeTimeframe}`).then(r => r.json()).then(ai => {
    if(activeSymbol !== sym) return;
    const badge = document.getElementById('aiBadge');
    badge.textContent = ai.signal;
    badge.className = `badge bg-${ai.signal.toLowerCase()}`;
    document.getElementById('aiRationale').textContent = ai.rationale;
    clearExtraSeries();
    if(ai.projection && ai.projection.length > 0 && data.bars?.length > 0) {
      const color = ai.signal === 'BUY' ? '#10b981' : (ai.signal === 'SELL' ? '#ef4444' : '#6b7280');
      addDashedSeries(ai.projection, color);
    }
  });

  if (!silentRefresh) refreshNews(sym);
}

async function updateWatchlistUI(refresh = false) {
  const res = await fetch(`/api/quotes?symbols=${watchlist.join(',')}${refresh ? '&refresh=1' : ''}`);
  const quotes = await res.json();
  const listEl = document.getElementById('symbolList');
  listEl.innerHTML = '';
  quotes.forEach(q => {
    const row = document.createElement('div');
    row.className = 'watch-item panel-2 border border-[var(--border)] px-2 py-1 flex justify-between items-center cursor-pointer hover:border-emerald-400';
    row.dataset.symbol = q.symbol;
    row.onclick = () => loadSymbol(q.symbol);
    row.innerHTML = `
        <span class="font-bold">${esc(q.symbol)}</span>
        <div class="flex items-center gap-2">
            <span>${q.price > 0 ? q.price.toFixed(2) : '—'}</span>
            <button class="text-red-500 hover:text-red-300 font-bold px-1" onclick="removeFromWatchlist('${esc(q.symbol)}', event)">×</button>
        </div>
    `;
    listEl.appendChild(row);
  });
  highlightWatchlist();

  const track = document.getElementById('tickerTrack');
  if (track) {
      const html = quotes.map(q => `<span class="text-gray-400">${esc(q.symbol)} <span class="text-emerald-300">${q.price.toFixed(2)}</span></span>`).join('');
      track.innerHTML = html + html;
  }
}

document.querySelectorAll('.tf-btn').forEach(btn => btn.addEventListener('click', () => loadSymbol(activeSymbol, btn.dataset.tf)));

const searchInput = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');
let searchTimeout;

searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  const q = e.target.value.trim();
  if(!q) { searchResults.classList.add('hidden'); return; }
  searchTimeout = setTimeout(async () => {
    const res = await fetch(`/api/search?q=${q}`);
    const matches = await res.json();
    searchResults.innerHTML = '';
    if(matches.length > 0) {
      matches.forEach(m => {
        const div = document.createElement('div');
        div.className = 'search-item';
        div.innerHTML = `<span class="font-bold text-emerald-400">${esc(m.symbol)}</span> <span class="text-gray-400 truncate ml-2">${esc(m.name)}</span>`;
        div.onclick = () => {
          if(!watchlist.includes(m.symbol)) { watchlist.push(m.symbol); localStorage.setItem('tcs_wl', JSON.stringify(watchlist)); updateWatchlistUI(); }
          loadSymbol(m.symbol); searchInput.value = ''; searchResults.classList.add('hidden');
        };
        searchResults.appendChild(div);
      });
      searchResults.classList.remove('hidden');
    } else searchResults.classList.add('hidden');
  }, 300);
});
document.addEventListener('click', (e) => { if(!searchInput.contains(e.target)) searchResults.classList.add('hidden'); });

async function fetchPortfolio() {
  const accRes = await fetch('/api/account');
  const acc = await accRes.json();
  if(!acc.error) {
    document.getElementById('portEquity').textContent = '$' + parseFloat(acc.equity).toFixed(2);
    document.getElementById('portBp').textContent = '$' + parseFloat(acc.buying_power).toFixed(2);
  }

  const posRes = await fetch('/api/positions');
  const pos = await posRes.json();

  const ordersRes = await fetch('/api/orders_pending');
  const orders = await ordersRes.json();

  const listEl = document.getElementById('positionsList');
  listEl.innerHTML = '';

  let hasItems = false;

  if(!pos.error && pos.length > 0) {
    hasItems = true;
    pos.forEach(p => {
      const pl = parseFloat(p.unrealized_pl);
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-[var(--border)]';
      row.innerHTML = `
        <div><span class="font-bold text-white">${esc(p.symbol)}</span> <span class="text-gray-500">${esc(p.qty)} sh</span></div>
        <div class="${pl>=0?'text-emerald-400':'text-red-400'}">${pl>=0?'+':''}${pl.toFixed(2)}</div>
      `;
      listEl.appendChild(row);
    });
  }

  if(!orders.error && orders.length > 0) {
    hasItems = true;
    orders.forEach(o => {
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-yellow-900/50 mt-1';
      row.innerHTML = `
        <div><span class="font-bold text-yellow-500">${esc(o.symbol)}</span> <span class="text-gray-500">${esc(o.qty)} sh (${esc(o.side.toUpperCase())})</span></div>
        <div class="text-yellow-600 bg-yellow-900/20 px-1 rounded">PENDING</div>
      `;
      listEl.appendChild(row);
    });
  }

  if(!hasItems) {
    listEl.innerHTML = '<div class="text-gray-600 italic">No open positions or pending orders.</div>';
  }
}

async function executeOrder(side) {
  const qtyInput = document.getElementById('tradeQty');
  const qty = parseInt(qtyInput.value) || 1;

  showToast('ORDER ROUTING', `Transmitting ${side.toUpperCase()} order for ${qty} shares of ${activeSymbol}...`, 'info');

  try {
      const res = await fetch('/api/order', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ symbol: activeSymbol, side: side, qty: qty })
      });
      const data = await res.json();

      if(data.error) {
          showToast('ORDER REJECTED', data.error, 'error');
      } else {
          showToast('ORDER PLACED', `Order sent. It may be pending if the market is closed.`, 'success');
          setTimeout(fetchPortfolio, 1000);
          setTimeout(fetchPortfolio, 3000);
          setTimeout(fetchPortfolio, 5000);
      }
  } catch (e) {
      showToast('SYSTEM ERROR', 'Order transmission failed check connection.', 'error');
  }
}

async function refreshNews(symbol) {
  const box = document.getElementById('newsList');
  box.innerHTML = '<div class="text-gray-500 text-center mt-4">Analyzing sentiment via LLM...</div>';

  const inject = document.getElementById('injectDemo')?.checked ? '&inject=1' : '';
  const res = await fetch(`/api/news?symbol=${symbol}${inject}`);
  const items = await res.json();
  box.innerHTML = '';

  if(items.length === 0) {
      box.innerHTML = '<div class="text-gray-600 italic text-center mt-4">No recent news available.</div>';
      return;
  }

  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'fade-in panel-2 border border-[var(--border)] p-2 mb-1';

    let badgeClass = 'bg-hold';
    if(it.sentiment === 'BULLISH') badgeClass = 'bg-buy';
    if(it.sentiment === 'BEARISH') badgeClass = 'bg-sell';

    if (it.blocked) {
      div.className += ' news-blocked';
      div.innerHTML = `
        <div class="flex justify-between items-start mb-1 gap-2">
          <span class="text-red-300 font-bold text-[10px] leading-tight flex-1">Headline quarantined by the injection filter</span>
          <span class="inj-chip">BLOCKED</span>
        </div>
        <div class="text-gray-500 text-[9px] leading-tight">
          Detected: ${esc((it.injection_flags || []).join(', '))}. Content was redacted before
          reaching any agent and never entered the model context.
        </div>
      `;
      box.appendChild(div);
      return;
    }

    div.innerHTML = `
      <div class="flex justify-between items-start mb-1 gap-2">
        <a href="${esc(it.link)}" target="_blank" class="text-emerald-300 font-bold hover:underline text-[10px] leading-tight flex-1">${esc(it.title)}</a>
        <span class="badge ${badgeClass} text-[8px] px-1">${esc(it.sentiment)}</span>
      </div>
      <div class="text-gray-500 text-[9px] leading-tight">${esc(it.summary || '')}</div>
    `;
    box.appendChild(div);
  });
}

async function runStrategy() {
  const btn = document.getElementById('strategyBtn');
  const original = btn.textContent;
  btn.textContent = 'ANALYZING...';
  btn.disabled = true;
  clearStrategy();

  try {
    const res = await fetch(`/api/strategy/${activeSymbol}?timeframe=${activeTimeframe}`);
    const s = await res.json();

    if (s.error) { showToast('STRATEGY', s.error, 'error'); return; }

    strategyLines.push(candleSeries.createPriceLine({
      price: s.entry, color: '#3b82f6', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true, title: `ENTRY ${s.entry}`
    }));
    strategyLines.push(candleSeries.createPriceLine({
      price: s.take_profit, color: '#10b981', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: `TP (+) ${s.take_profit}`
    }));
    strategyLines.push(candleSeries.createPriceLine({
      price: s.stop_loss, color: '#ef4444', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: `SL (-) ${s.stop_loss}`
    }));

    const color = s.signal === 'BUY' ? '#10b981' : (s.signal === 'SELL' ? '#ef4444' : '#9ca3af');
    const shape = s.signal === 'BUY' ? 'arrowUp' : (s.signal === 'SELL' ? 'arrowDown' : 'circle');
    const position = s.signal === 'SELL' ? 'aboveBar' : 'belowBar';
    candleSeries.setMarkers([{ time: s.entry_time, position, color, shape, text: s.signal }]);

    showStrategyPanel(s);
  } catch (e) {
    showToast('STRATEGY', 'Could not build the strategy. Check the connection.', 'error');
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function advisorList(items, color) {
  return (items || []).length ? items.map(item => `<div class="${color} text-[9px] leading-snug mb-1">• ${esc(item)}</div>`).join('') : '<div class="text-gray-600 text-[9px]">No decisive evidence.</div>';
}

function showAdvisorPanel(a, challenge = false) {
  const panel = document.getElementById('advisorPanel');
  if (a.error) { showToast('AI ADVISOR', a.error, 'error'); return; }
  const f = a.facts;
  const setup = a.trade_setup || {};
  const challengeHtml = challenge && a.challenge ? `
    <div class="border-t border-rose-900 mt-2 pt-2">
      <div class="text-rose-300 font-bold text-[10px] uppercase mb-1">Pre-Trade Challenge Report</div>
      ${Object.entries(a.challenge).map(([k,v]) => `<div class="text-[9px] leading-snug mb-1"><span class="text-gray-500 uppercase">${esc(k.replaceAll('_', ' '))}:</span> ${esc(v)}</div>`).join('')}
    </div>` : '';
  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2"><span class="text-cyan-300 font-bold text-[10px] uppercase">AI Strategy Assessment · ${esc(a.symbol)}</span><button onclick="document.getElementById('advisorPanel').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm">×</button></div>
    <div class="grid grid-cols-2 gap-1 text-[9px] mb-2"><div class="bg-black p-2 border border-cyan-900"><div class="text-gray-500">STRATEGY QUALITY</div><div class="text-cyan-300 text-lg font-bold">${a.quality_score}<span class="text-[9px]">/100</span></div><div class="text-gray-500">${esc(a.quality_label)} · not a profit probability</div></div><div class="bg-black p-2 border border-cyan-900"><div class="text-gray-500">MARKET REGIME</div><div class="text-white font-bold mt-1">${esc(a.regime)}</div><div class="text-gray-500 mt-1">ATR ${f.atr_pct}% · RSI ${f.rsi14 ?? '—'}</div></div></div>
    <div class="grid grid-cols-2 gap-2"><div><div class="text-emerald-300 font-bold text-[9px] uppercase mb-1">Bull Case</div>${advisorList(a.bull_case, 'text-emerald-200')}</div><div><div class="text-red-300 font-bold text-[9px] uppercase mb-1">Bear Case</div>${advisorList(a.bear_case, 'text-red-200')}</div></div>
    <div class="border-t border-[var(--border)] mt-2 pt-2 text-[9px]"><div class="text-gray-500 uppercase">Score Method · ${esc(a.strategy.name)}</div>${advisorList(a.strategy.rules, 'text-gray-400')}<div class="text-gray-500 mt-1">Score combines the rules above with setup R:R and ATR volatility; unavailable or conflicting evidence reduces the score.</div></div>
    <div class="border-t border-[var(--border)] mt-2 pt-2 text-[9px]"><div class="text-gray-500 uppercase">Trade Setup</div><div class="grid grid-cols-4 gap-1 mt-1 text-center"><div>Entry<br><b class="text-blue-300">${setup.entry ?? '—'}</b></div><div>Stop<br><b class="text-red-300">${setup.stop_loss ?? '—'}</b></div><div>Target<br><b class="text-emerald-300">${setup.take_profit ?? '—'}</b></div><div>R:R<br><b>${setup.risk_reward ?? '—'}</b></div></div><div class="text-amber-200 mt-2">Confirm: ${esc(a.confirmation_needed)}</div><div class="text-red-200 mt-1">Invalidation: ${esc(a.invalidation)}</div></div>
    ${challengeHtml}<div class="text-gray-600 text-[8px] mt-2">${esc(a.disclaimer)}</div>`;
  panel.classList.remove('hidden');
}

async function runAdvisor() {
  const btn = document.getElementById('advisorBtn'); btn.disabled = true; btn.textContent = 'ANALYZING...';
  try { const r = await fetch(`/api/advisor/${activeSymbol}?timeframe=${activeTimeframe}&track=1`); showAdvisorPanel(await r.json()); }
  catch (e) { showToast('AI ADVISOR', 'Could not build an assessment.', 'error'); }
  finally { btn.disabled = false; btn.textContent = 'AI ADVISOR'; }
}

function showChallengePanel(a) {
  const panel = document.getElementById('advisorPanel');
  if (a.error) { showToast('CHALLENGE', a.error, 'error'); return; }
  const c = a.challenge;
  panel.innerHTML = `<div class="flex justify-between mb-2"><span class="text-rose-300 font-bold text-[10px] uppercase">Challenge My Strategy · ${esc(a.symbol)}</span><button onclick="document.getElementById('advisorPanel').classList.add('hidden')" class="text-gray-500">×</button></div><div class="text-[9px] text-gray-400 mb-2">Test how the quality score changes when you apply a different strategy rule set.</div><label class="text-[9px] text-gray-500">STRATEGY TO TEST<select id="challengeMode" onchange="challengeStrategy(this.value)" class="w-full mt-1 bg-black border border-rose-900 p-1 text-white">${a.strategy_options.map(o => `<option value="${esc(o.id)}" ${o.id === a.strategy.id ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label><div class="grid grid-cols-2 gap-2 mt-2"><div class="bg-black border border-rose-900 p-2"><div class="text-gray-500 text-[9px]">REVISED QUALITY</div><div class="text-rose-300 text-xl font-bold">${a.quality_score}<span class="text-[9px]">/100</span></div><div class="text-gray-500 text-[9px]">${esc(a.strategy.name)}</div></div><div class="bg-black border border-rose-900 p-2 text-[9px]"><div class="text-gray-500">RULES TESTED</div>${advisorList(a.strategy.rules, 'text-gray-300')}</div></div><div class="border-t border-rose-900 mt-2 pt-2">${Object.entries(c).map(([k,v]) => `<div class="text-[9px] leading-snug mb-2"><span class="text-rose-300 uppercase">${esc(k.replaceAll('_', ' '))}:</span><br>${esc(v)}</div>`).join('')}</div><div class="text-gray-600 text-[8px]">This is a rule-based stress test using available price/volume data, not a guarantee or a forecast.</div>`;
  panel.classList.remove('hidden');
}

async function challengeStrategy(mode = 'momentum') {
  const btn = document.getElementById('challengeBtn'); btn.disabled = true; btn.textContent = 'TESTING...';
  try { const r = await fetch(`/api/advisor/${activeSymbol}/challenge?timeframe=${activeTimeframe}&strategy=${encodeURIComponent(mode)}`); showChallengePanel(await r.json()); }
  catch (e) { showToast('CHALLENGE', 'Could not challenge this setup.', 'error'); }
  finally { btn.disabled = false; btn.textContent = 'CHALLENGE'; }
}

async function openPredictionMetrics() {
  const panel = document.getElementById('riskPanel');
  panel.innerHTML = '<div class="text-slate-200 font-bold text-[10px] uppercase">AI Prediction Metrics</div><div class="text-gray-500 text-[9px] mt-2">Refreshing tracked directional calls…</div>';
  panel.classList.remove('hidden');
  try {
    const d = await (await fetch('/api/prediction-metrics')).json();
    const success = d.success_pct == null ? '—' : d.success_pct + '%';
    const note = d.resolved_calls ? 'A win means the tracked BUY/SELL direction was correct at its planned evaluation time.' : 'No completed calls yet. Make an AI Advisor assessment and allow its evaluation window to finish.';
    panel.innerHTML = `<div class="flex justify-between"><span class="text-slate-200 font-bold text-[10px] uppercase">AI Prediction Metrics</span><button onclick="document.getElementById('riskPanel').classList.add('hidden')" class="text-gray-500">×</button></div><div class="grid grid-cols-3 gap-1 text-center text-[9px] mt-3"><div>SUCCESS<br><b class="text-cyan-300 text-lg">${success}</b></div><div>RESOLVED<br><b>${d.resolved_calls}</b></div><div>PENDING<br><b>${d.pending_calls}</b></div><div>WINS<br><b class="text-emerald-300">${d.wins}</b></div><div>TRACKED<br><b>${d.tracked_calls}</b></div><div>AVG QUALITY<br><b>${d.average_quality ?? '—'}</b></div></div><div class="text-gray-500 text-[9px] mt-3 leading-snug">${note} This measures tracked directional calls, not future-profit probability.</div>`;
  } catch (e) { panel.innerHTML = '<div class="text-red-300 text-[9px]">Metrics could not be loaded.</div>'; }
}

function openRiskCalculator() {
  const panel = document.getElementById('riskPanel');
  const price = document.getElementById('activePrice').textContent || '';
  panel.innerHTML = `<div class="flex justify-between mb-2"><span class="text-slate-200 font-bold text-[10px] uppercase">Risk & Position Size</span><button onclick="document.getElementById('riskPanel').classList.add('hidden')" class="text-gray-500">×</button></div><div class="grid grid-cols-2 gap-2 text-[9px]"><label>Account $<input id="riskAccount" value="10000" type="number" min="1" class="w-full bg-black border border-[var(--border)] p-1 text-white"></label><label>Max risk %<input id="riskPct" value="1" type="number" min="0.01" step="0.1" class="w-full bg-black border border-[var(--border)] p-1 text-white"></label><label>Entry<input id="riskEntry" value="${esc(price)}" type="number" step="0.01" class="w-full bg-black border border-[var(--border)] p-1 text-white"></label><label>Stop<input id="riskStop" type="number" step="0.01" class="w-full bg-black border border-[var(--border)] p-1 text-white"></label><label>Target<input id="riskTarget" type="number" step="0.01" class="w-full bg-black border border-[var(--border)] p-1 text-white"></label></div><button onclick="calculateRisk()" class="mt-3 bg-slate-200 text-black font-bold px-3 py-1 text-[10px]">CALCULATE</button><div id="riskResult" class="mt-2 text-[9px]"></div>`;
  panel.classList.remove('hidden');
}

async function calculateRisk() {
  const body = { account_size: document.getElementById('riskAccount').value, risk_pct: document.getElementById('riskPct').value, entry: document.getElementById('riskEntry').value, stop: document.getElementById('riskStop').value, target: document.getElementById('riskTarget').value };
  const r = await fetch('/api/risk/calculate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); const d = await r.json();
  document.getElementById('riskResult').innerHTML = d.error ? `<span class="text-red-300">${esc(d.error)}</span>` : `<div class="grid grid-cols-3 gap-1"><div>Shares<br><b class="text-white">${d.position_size}</b></div><div>Max loss<br><b class="text-red-300">$${d.maximum_loss}</b></div><div>Potential profit<br><b class="text-emerald-300">$${d.potential_profit}</b></div><div>Position value<br><b>$${d.position_value}</b></div><div>R:R<br><b>${d.risk_reward}</b></div><div>Risk budget<br><b>$${d.max_risk_amount}</b></div></div>`;
}

async function openRegulationPanel() {
  const panel = document.getElementById('regulationPanel');
  panel.innerHTML = '<div class="text-orange-300 font-bold text-[10px] uppercase">Regulation</div><div class="text-gray-500 text-[9px] mt-2">Loading rules…</div>';
  panel.classList.remove('hidden');
  await refreshRegulationPanel();
}

async function refreshRegulationPanel() {
  const panel = document.getElementById('regulationPanel');
  if (panel.classList.contains('hidden')) return;
  try {
    const [regRes, violRes] = await Promise.all([
      fetch('/api/regulations'), fetch('/api/regulations/violations')
    ]);
    const d = await regRes.json();
    const violations = await violRes.json();
    renderRegulationPanel(d.rules, d.pdt, violations);
  } catch (e) {
    panel.innerHTML = '<div class="text-red-300 text-[9px]">Could not load regulations.</div>';
  }
}

function renderRegulationPanel(rules, pdt, violations) {
  const panel = document.getElementById('regulationPanel');
  const restricted = rules.filter(r => r.rule_type === 'restricted_symbol');
  const caps = rules.filter(r => r.rule_type === 'position_cap');

  const ruleRow = (r, valueLabel) => `
    <div class="flex justify-between items-center text-[9px] py-1 border-b border-[var(--border)]">
      <div class="flex items-center gap-2">
        <input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="toggleRegulationRule(${r.id}, this.checked)" class="accent-orange-500">
        <span class="text-white font-bold">${esc(r.symbol)}</span>
        <span class="text-gray-500">${esc(valueLabel(r))}</span>
      </div>
      <button onclick="removeRegulationRule(${r.id})" class="text-gray-500 hover:text-red-400">×</button>
    </div>`;

  const pdtColor = pdt.at_limit ? 'text-red-400' : (pdt.restricted ? 'text-amber-400' : 'text-emerald-400');

  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="text-orange-300 font-bold text-[10px] uppercase">Regulation</span>
      <button onclick="document.getElementById('regulationPanel').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>

    <div class="bg-black border border-orange-900 p-2 mb-3">
      <div class="text-gray-500 text-[9px] uppercase mb-1">Pattern Day Trader (FINRA 4210)</div>
      <div class="grid grid-cols-3 gap-1 text-center text-[9px]">
        <div>EQUITY<br><b class="${pdt.restricted ? 'text-amber-400' : 'text-white'}">$${Number(pdt.equity).toLocaleString()}</b></div>
        <div>DAY TRADES<br><b class="${pdtColor}">${pdt.day_trades_used}/${pdt.day_trade_limit}</b></div>
        <div>STATUS<br><b class="${pdtColor}">${pdt.at_limit ? 'BLOCKED' : (pdt.restricted ? 'MONITORED' : 'CLEAR')}</b></div>
      </div>
      <div class="text-gray-600 text-[8px] mt-1">Accounts under $${Number(pdt.equity_floor).toLocaleString()} equity are limited to ${pdt.day_trade_limit} day trades per rolling ${pdt.window_days} sessions.</div>
    </div>

    <div class="mb-3">
      <div class="text-gray-500 text-[9px] uppercase mb-1">Restricted symbols</div>
      <div class="flex gap-1 mb-1">
        <input id="regRestrictSymbol" placeholder="SYMBOL" class="w-20 bg-black border border-[var(--border)] p-1 text-white text-[9px] uppercase">
        <input id="regRestrictReason" placeholder="Reason (optional)" class="flex-1 bg-black border border-[var(--border)] p-1 text-white text-[9px]">
        <button onclick="addRestrictedSymbol()" class="bg-orange-700 hover:bg-orange-600 text-white px-2 text-[9px] font-bold">ADD</button>
      </div>
      <div>${restricted.length ? restricted.map(r => ruleRow(r, x => x.param || 'no reason given')).join('') : '<div class="text-gray-600 text-[9px] italic">None.</div>'}</div>
    </div>

    <div class="mb-3">
      <div class="text-gray-500 text-[9px] uppercase mb-1">Position caps</div>
      <div class="flex gap-1 mb-1">
        <input id="regCapSymbol" placeholder="SYMBOL" class="w-20 bg-black border border-[var(--border)] p-1 text-white text-[9px] uppercase">
        <input id="regCapQty" type="number" min="1" placeholder="Max shares" class="flex-1 bg-black border border-[var(--border)] p-1 text-white text-[9px]">
        <button onclick="addPositionCap()" class="bg-orange-700 hover:bg-orange-600 text-white px-2 text-[9px] font-bold">ADD</button>
      </div>
      <div>${caps.length ? caps.map(r => ruleRow(r, x => 'max ' + x.param + ' sh')).join('') : '<div class="text-gray-600 text-[9px] italic">None.</div>'}</div>
    </div>

    <div>
      <div class="text-gray-500 text-[9px] uppercase mb-1">Recent violations</div>
      ${violations.length ? violations.slice(0, 8).map(v => {
        let detail = {};
        try { detail = JSON.parse(v.detail || '{}'); } catch (e) {}
        return `<div class="text-[9px] text-red-300 leading-snug border-b border-[var(--border)] py-1"><span class="text-gray-500">${esc(v.ts)}</span> ${esc(detail.side || '')} ${esc(detail.qty ?? '')} ${esc(detail.symbol || '')} — ${esc(detail.reason || '')}</div>`;
      }).join('') : '<div class="text-gray-600 text-[9px] italic">None recorded.</div>'}
    </div>
  `;
}

async function addRestrictedSymbol() {
  const symbol = document.getElementById('regRestrictSymbol').value;
  const param = document.getElementById('regRestrictReason').value;
  if (!symbol) { showToast('REGULATION', 'Enter a symbol first.', 'error'); return; }
  const res = await fetch('/api/regulations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rule_type: 'restricted_symbol', symbol, param})});
  const d = await res.json();
  if (d.error) { showToast('REGULATION', d.error, 'error'); return; }
  showToast('REGULATION', `${symbol.toUpperCase()} added to the restricted list.`, 'success');
  refreshRegulationPanel();
}

async function addPositionCap() {
  const symbol = document.getElementById('regCapSymbol').value;
  const param = document.getElementById('regCapQty').value;
  if (!symbol || !param) { showToast('REGULATION', 'Enter a symbol and a share limit.', 'error'); return; }
  const res = await fetch('/api/regulations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rule_type: 'position_cap', symbol, param})});
  const d = await res.json();
  if (d.error) { showToast('REGULATION', d.error, 'error'); return; }
  showToast('REGULATION', `Position cap set for ${symbol.toUpperCase()}.`, 'success');
  refreshRegulationPanel();
}

async function removeRegulationRule(id) {
  await fetch(`/api/regulations/${id}`, {method: 'DELETE'});
  refreshRegulationPanel();
}

async function toggleRegulationRule(id, enabled) {
  await fetch(`/api/regulations/${id}/toggle`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled})});
  refreshRegulationPanel();
}

function showStrategyPanel(s) {
  const panel = document.getElementById('strategyPanel');
  const badgeClass = s.signal === 'BUY' ? 'bg-buy' : (s.signal === 'SELL' ? 'bg-sell' : 'bg-hold');
  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="badge ${badgeClass}">${esc(s.signal)}</span>
      <span class="text-[9px] text-gray-500">R:R ${s.risk_reward ?? '—'}</span>
      <button onclick="clearStrategy()" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>
    <div class="grid grid-cols-3 gap-1 text-center text-[9px] mb-2">
      <div><div class="text-gray-500">ENTRY</div><div class="text-blue-400 font-bold">${s.entry}</div></div>
      <div><div class="text-gray-500">TP (+)</div><div class="text-emerald-400 font-bold">${s.take_profit}</div></div>
      <div><div class="text-gray-500">SL (-)</div><div class="text-red-400 font-bold">${s.stop_loss}</div></div>
    </div>
    <div class="text-[9px] text-gray-400 mb-2 leading-snug">${esc(s.rationale)}</div>
    <div class="text-[9px] text-emerald-300 mb-1 leading-snug">▲ ${esc(s.positive_scenario)}</div>
    <div class="text-[9px] text-red-300 leading-snug">▼ ${esc(s.negative_scenario)}</div>
  `;
  panel.classList.remove('hidden');
}

async function runBacktest() {
  const btn = document.getElementById('backtestBtn');
  const original = btn.textContent;
  btn.textContent = 'SIMULATING...';
  btn.disabled = true;
  document.getElementById('strategyPanel').classList.add('hidden');

  try {
    const res = await fetch(`/api/backtest/${activeSymbol}?timeframe=${activeTimeframe}`);
    const bt = await res.json();
    if (bt.error) { showToast('BACKTEST', bt.error, 'error'); return; }
    showBacktestPanel(bt);
  } catch (e) {
    showToast('BACKTEST', 'Could not run the simulation.', 'error');
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function showBacktestPanel(bt) {
  const panel = document.getElementById('backtestPanel');
  const pnlColor = bt.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
  const beats = bt.beats_buy_and_hold;
  const rows = (bt.trades || []).map(t => `
    <div class="flex justify-between text-[9px] border-b border-[#111827] py-0.5">
      <span class="text-gray-500">${esc(t.entry_time)} → ${esc(t.exit_time)}</span>
      <span class="${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}">${t.pnl >= 0 ? '+' : ''}${t.pnl} (${t.pnl_pct}%)</span>
    </div>`).join('');

  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="text-violet-300 font-bold text-[10px] uppercase tracking-wider">Backtest · ${esc(bt.symbol)}</span>
      <button onclick="document.getElementById('backtestPanel').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>
    <div class="text-[9px] text-gray-500 mb-2">${esc(bt.strategy)} · ${bt.periods_tested} periods</div>
    <div class="grid grid-cols-3 gap-1 text-center text-[9px] mb-2">
      <div><div class="text-gray-500">THEORETICAL PnL</div><div class="${pnlColor} font-bold">$${bt.total_pnl}</div></div>
      <div><div class="text-gray-500">RETURN</div><div class="${pnlColor} font-bold">${bt.total_pnl_pct}%</div></div>
      <div><div class="text-gray-500">WIN RATE</div><div class="text-white font-bold">${bt.win_rate_pct}%</div></div>
    </div>
    <div class="text-[9px] mb-2 p-1 border ${beats ? 'border-emerald-800 text-emerald-300' : 'border-red-900 text-red-300'}">
      ${beats ? '▲' : '▼'} Strategy ${bt.total_pnl_pct}% vs Buy &amp; Hold ${bt.buy_and_hold_pct}%
    </div>
    <div class="text-[9px] text-gray-500 mb-1">${bt.num_trades} trades · best $${bt.best_trade} · worst $${bt.worst_trade}</div>
    <div class="max-h-24 overflow-y-auto">${rows || '<div class="text-gray-600 italic text-[9px]">No crossovers in the period.</div>'}</div>
  `;
  panel.classList.remove('hidden');
}

async function runOptimizer() {
  const btn = document.getElementById('optimizeBtn');
  const original = btn.textContent;
  btn.textContent = 'SWEEPING...';
  btn.disabled = true;
  document.getElementById('strategyPanel').classList.add('hidden');
  document.getElementById('backtestPanel').classList.add('hidden');

  try {
    const res = await fetch(`/api/optimize/${activeSymbol}?timeframe=${activeTimeframe}`);
    const opt = await res.json();
    if (opt.error) { showToast('OPTIMIZER', opt.error, 'error'); return; }
    showOptimizerPanel(opt);
  } catch (e) {
    showToast('OPTIMIZER', 'Could not run the parameter sweep.', 'error');
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function showOptimizerPanel(opt) {
  const panel = document.getElementById('optimizerPanel');
  const b = opt.best;
  const rows = opt.leaderboard.map(r => {
    const isBest = r.fast === b.fast && r.slow === b.slow;
    const isDef = opt.default && r.fast === opt.default.fast && r.slow === opt.default.slow;
    const cls = isBest ? 'opt-best' : (isDef ? 'opt-default' : '');
    return `<tr class="${cls}">
      <td>SMA ${r.fast}x${r.slow}${isDef ? ' (default)' : ''}</td>
      <td class="${r.total_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}">${r.total_pnl_pct}%</td>
      <td>${r.excess_return_pct}%</td>
      <td>${r.sharpe}</td>
      <td>${r.max_drawdown_pct}%</td>
      <td>${r.num_trades}</td>
    </tr>`;
  }).join('');

  const imp = opt.improvement_vs_default_pct;
  const impLine = (imp === null || imp === undefined) ? '' :
    `<div class="text-[10px] mb-2 p-1 border ${imp > 0 ? 'border-emerald-800 text-emerald-300' : 'border-gray-700 text-gray-400'}">
       Tuning changed return by <b>${imp > 0 ? '+' : ''}${imp} pp</b> versus the default SMA 10x30.
     </div>`;

  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="text-amber-300 font-bold text-[10px] uppercase tracking-wider">Strategy Sweep · ${esc(opt.symbol)}</span>
      <button onclick="document.getElementById('optimizerPanel').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>
    <div class="text-[9px] text-gray-500 mb-2">
      ${opt.combinations_tested} parameter combinations backtested over ${opt.lookback} candles ·
      buy &amp; hold ${opt.buy_and_hold_pct}%
    </div>
    ${impLine}
    <div class="max-h-52 overflow-y-auto">
      <table class="opt-table">
        <thead><tr><th>Parameters</th><th>Return</th><th>Excess</th><th>Sharpe</th><th>MaxDD</th><th>Trades</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="text-[9px] text-gray-600 mt-2 leading-snug">
      Ranked by risk-adjusted score (Sharpe, excess return, drawdown penalty).
      Historical simulation only — not a forecast. Confidence on winner: <b class="badge conf-${esc(b.confidence)}">${esc(b.confidence)}</b>
    </div>
  `;
  panel.classList.remove('hidden');
}

async function setScenario(value) {
  try {
    const res = await fetch('/api/sim/scenario', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ scenario: value })
    });
    const data = await res.json();
    showToast('MARKET MODE', data.message, 'info');
    clearStrategy();
    await loadSymbol(activeSymbol, activeTimeframe);
    await updateWatchlistUI();
    fetchPortfolio();
  } catch (e) {
    showToast('MARKET MODE', 'Could not switch market mode.', 'error');
  }
}

// ------------------------------------------------------------------
// CHAT + AGENT TRACE + HUMAN-IN-THE-LOOP
// ------------------------------------------------------------------
function appendChat(role, text) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in ${role==='user'?'chat-msg-user':'chat-msg-ai'}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function buildTraceBox(trace, metrics) {
  const det = document.createElement('details');
  det.className = 'trace-box';
  const agentsUsed = [...new Set(trace.map(t => t.agent))].length;

  const m = metrics || {};
  const meter = `
    <div class="meter-row">
      <span>latency <b>${m.total_ms != null ? (m.total_ms/1000).toFixed(1) + 's' : '—'}</b></span>
      <span>llm calls <b>${m.llm_calls ?? '—'}</b></span>
      <span>tokens <b>${m.tokens_total ?? '—'}</b> (${m.tokens_in ?? 0} in / ${m.tokens_out ?? 0} out)</span>
      <span>agents <b>${m.agents ?? agentsUsed}</b></span>
      ${m.injection_blocks ? `<span class="text-red-400">injection blocked <b>${m.injection_blocks}</b></span>` : ''}
      ${m.escalations ? `<span class="text-yellow-400">escalations <b>${m.escalations}</b></span>` : ''}
    </div>`;

  const steps = trace.map(t => `
    <div class="trace-step ${t.level > 0 ? 'trace-rail' : ''}" style="margin-left:${t.level * 12}px">
      <div class="trace-head">
        <span class="trace-agent">${esc(t.agent)}</span>
        <span class="trace-tool">${esc(t.tool)}</span>
        <span class="trace-ts">${t.latency_ms != null ? t.latency_ms + 'ms · ' : ''}${esc(t.ts)}</span>
      </div>
      ${t.input ? `<div class="trace-io"><span class="trace-label">IN</span>${esc(t.input)}</div>` : ''}
      ${t.output ? `<div class="trace-io"><span class="trace-label">OUT</span>${esc(t.output)}</div>` : ''}
    </div>`).join('');

  det.innerHTML = `<summary>▸ Agent Trace · ${trace.length} steps · ${agentsUsed} agents`
    + `${m.total_ms != null ? ' · ' + (m.total_ms/1000).toFixed(1) + 's' : ''}`
    + `${m.tokens_total ? ' · ' + m.tokens_total + ' tokens' : ''}</summary>`
    + meter + steps;
  return det;
}

function buildComparison(agentic, baseline) {
  const wrap = document.createElement('div');
  wrap.className = 'cmp-grid';
  wrap.innerHTML = `
    <div class="cmp-col cmp-agentic">
      <h4>Agentic desk (analyst + optimizer + CRO)</h4>
      <div>${esc(agentic)}</div>
    </div>
    <div class="cmp-col cmp-baseline">
      <h4>Baseline: single model, no tools</h4>
      <div>${esc(baseline)}</div>
    </div>
  `;
  return wrap;
}

function buildHitlCard(p) {
  const card = document.createElement('div');
  card.className = 'hitl-card';
  card.innerHTML = `
    <div class="hitl-title">⚠ Guardrail · Human approval required</div>
    <div class="hitl-detail">
      The agent tried to execute <b class="text-white">${esc(p.side.toUpperCase())} ${p.qty} ${esc(p.symbol)}</b>,
      which is above the auto-execution limit. The order was NOT sent to the broker.
      <span class="text-gray-500">Ticket ${esc(p.id)} · ${esc(p.created)}</span>
    </div>
    <div class="flex gap-2">
      <button class="hitl-btn hitl-approve">✓ APPROVE</button>
      <button class="hitl-btn hitl-reject">✕ REJECT</button>
    </div>
    <div class="hitl-status text-[9px] mt-2"></div>
  `;
  const [approveBtn, rejectBtn] = card.querySelectorAll('.hitl-btn');
  const status = card.querySelector('.hitl-status');

  async function resolve(decision) {
    approveBtn.disabled = true; rejectBtn.disabled = true;
    status.textContent = 'Processing...';
    status.className = 'hitl-status text-[9px] mt-2 text-gray-400';
    try {
      const res = await fetch('/api/trade/resolve', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ id: p.id, decision })
      });
      const data = await res.json();
      if (data.error) {
        status.textContent = data.error;
        status.className = 'hitl-status text-[9px] mt-2 text-red-400';
        return;
      }
      status.textContent = data.message;
      status.className = `hitl-status text-[9px] mt-2 ${data.status === 'executed' ? 'text-emerald-400' : 'text-yellow-400'}`;
      showToast('HUMAN-IN-THE-LOOP', data.message, data.status === 'executed' ? 'success' : 'info');
      if (data.status === 'executed') {
        setTimeout(fetchPortfolio, 1000);
        setTimeout(fetchPortfolio, 4000);
      }
    } catch (e) {
      status.textContent = 'Connection error while resolving the ticket.';
      status.className = 'hitl-status text-[9px] mt-2 text-red-400';
      approveBtn.disabled = false; rejectBtn.disabled = false;
    }
  }

  approveBtn.onclick = () => resolve('approve');
  rejectBtn.onclick = () => resolve('reject');
  return card;
}

function renderAgentReply(data) {
  const box = document.getElementById('chatMessages');
  const wrap = document.createElement('div');
  wrap.className = 'fade-in chat-msg-ai';

  const body = document.createElement('div');
  body.textContent = data.reply;
  wrap.appendChild(body);

  if (data.baseline) {
    body.textContent = '';
    wrap.appendChild(buildComparison(data.reply, data.baseline));
  }
  if (data.trace && data.trace.length) wrap.appendChild(buildTraceBox(data.trace, data.metrics));
  if (data.pending_approval) wrap.appendChild(buildHitlCard(data.pending_approval));

  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

document.getElementById('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if(!msg) return;
  appendChat('user', msg);
  input.value = '';
  const thinking = appendChat('ai', 'Portfolio Manager coordinating the Data Analyst and the CRO...');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        message: msg,
        compare_baseline: document.getElementById('compareBaseline')?.checked || false,
        inject_demo: document.getElementById('injectDemo')?.checked || false
      })
    });
    const data = await res.json();
    thinking.remove();
    renderAgentReply(data);
  } catch (err) {
    thinking.textContent = '⚠️ Connection error with the agent desk.';
  }

  setTimeout(fetchPortfolio, 2000);
  setTimeout(fetchPortfolio, 5000);
});

async function syncConfig() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    const sel = document.getElementById('simScenario');
    if (sel) sel.value = cfg.synthetic_mode ? cfg.synthetic_scenario : 'live';
  } catch (e) { /* non-fatal */ }
}

initChart();
initRsiChart();
initStudyCharts();
syncConfig();
loadSymbol(activeSymbol, "1Day");
updateWatchlistUI();
fetchPortfolio();
appendChat('ai', 'A2A Trade Terminal ready. I am the Portfolio Manager. I coordinate three specialists: the Data Analyst (prices, screened news, technicals), the Strategy Optimizer (parameter sweep across the moving-average grid) and the Chief Risk Officer (independent backtest verification and risk verdict). I can execute trades, but orders above the desk limit — or where my specialists disagree — are held for your approval.');
setInterval(fetchPortfolio, 15000);
let liveRefreshBusy = false;
setInterval(async () => {
  if (liveRefreshBusy) return;
  liveRefreshBusy = true;
  try { await loadSymbol(activeSymbol, null, true); } catch (e) { /* retain the last valid chart */ }
  liveRefreshBusy = false;
}, 30000);
setInterval(() => updateWatchlistUI(true), 30000);
</script>
</body>
</html>
"""