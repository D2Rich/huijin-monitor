// Cloudflare Worker: 给 ChatGPT 用的数据代理 (上交所接口需要 Referer 头, ChatGPT 自己加不了)
// 部署: Cloudflare Dashboard → Workers & Pages → Create → Hello World → 把本文件内容粘进去 → Deploy
// 得到 https://<name>.<你的子域>.workers.dev 后, ChatGPT 直接访问下面几个路径:
//   /sse?date=2026-09-17                       上交所全部 ETF 当日份额 {code: 万份}
//   /sse?date=2026-09-17&codes=510300,510050    只要指定代码
//   /szse?codes=159915,159919,159922,159845     深交所当前份额 (万份) + 数据日期
//   /cffex?date=20260917&prod=IF                中金所持仓排名 XML 原样返回 (prod=IF/IH/IC/IM)
//   /index?code=sh000001&n=30                   腾讯指数日线 (日期,开,收,高,低)
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36";
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" } });

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const p = url.pathname.replace(/\/+$/, "");
    try {
      if (p === "/sse") {
        const date = url.searchParams.get("date");
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "")) return json({ error: "date=YYYY-MM-DD" }, 400);
        const codes = (url.searchParams.get("codes") || "").split(",").filter(Boolean);
        const u = "http://query.sse.com.cn/commonQuery.do?isPagination=true&pageHelp.pageSize=1000&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.endPage=1&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE=" + date;
        const r = await fetch(u, { headers: { "User-Agent": UA, "Referer": "http://www.sse.com.cn/" } });
        const txt = await r.text();
        let j; try { j = JSON.parse(txt.replace(/^\s*\(?/, "").replace(/\)?\s*$/, "")); } catch (e) { return json({ error: "sse parse", raw: txt.slice(0, 200) }, 502); }
        const rows = (j.pageHelp && j.pageHelp.data) || [];
        const shares = {};
        for (const row of rows) if (!codes.length || codes.includes(row.SEC_CODE)) shares[row.SEC_CODE] = { name: row.SEC_NAME, shares_wan: Number(row.TOT_VOL) };
        return json({ date, count: rows.length, unit: "万份", shares });
      }
      if (p === "/szse") {
        const codes = (url.searchParams.get("codes") || "159915").split(",").filter(Boolean);
        const out = {}; let asof = null;
        for (const code of codes) {
          const u = "http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945&TABKEY=tab1&PAGENO=1&txtQueryKeyAndJC=" + code + "&random=" + Math.random();
          const r = await fetch(u, { headers: { "User-Agent": UA, "Referer": "http://www.szse.cn/" } });
          const j = await r.json();
          const rows = (j[0] && j[0].data) || [];
          asof = (j[0] && j[0].metadata && j[0].metadata.subname) || asof;
          for (const row of rows) {
            const c = String(row.sys_key).replace(/<[^>]+>/g, "");
            if (c === code) out[code] = { name: String(row.kzjcurl).replace(/<[^>]+>/g, "").trim().split(/\s+/)[0], shares_wan: Number(String(row.dqgm).replace(/,/g, "")) };
          }
        }
        return json({ asof, unit: "万份", note: "深交所只提供当前值", shares: out });
      }
      if (p === "/cffex") {
        const date = url.searchParams.get("date"); const prod = (url.searchParams.get("prod") || "IF").toUpperCase();
        if (!/^\d{8}$/.test(date || "") || !["IF", "IH", "IC", "IM"].includes(prod)) return json({ error: "date=YYYYMMDD&prod=IF|IH|IC|IM" }, 400);
        const u = `http://www.cffex.com.cn/sj/ccpm/${date.slice(0, 6)}/${date.slice(6)}/${prod}.xml`;
        const r = await fetch(u, { headers: { "User-Agent": UA, "Referer": "http://www.cffex.com.cn/ccpm/" } });
        const txt = await r.text();
        if (!txt.includes("<data")) return json({ error: "no data (非交易日或未发布)", status: r.status }, 404);
        return new Response(txt, { headers: { "content-type": "application/xml; charset=utf-8", "access-control-allow-origin": "*" } });
      }
      if (p === "/index") {
        const code = url.searchParams.get("code") || "sh000001"; const n = Number(url.searchParams.get("n") || 30);
        const end = new Date(); const start = new Date(end.getTime() - n * 1.6 * 86400e3);
        const f = d => d.toISOString().slice(0, 10);
        const u = `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${code},day,${f(start)},${f(end)},${n},qfq`;
        const r = await fetch(u, { headers: { "User-Agent": UA } });
        const j = await r.json(); const x = j.data[code]; const rows = x.qfqday || x.day || [];
        return json({ code, rows: rows.map(k => ({ date: k[0], open: +k[1], close: +k[2], high: +k[3], low: +k[4] })) });
      }
      return json({ routes: ["/sse?date=YYYY-MM-DD[&codes=a,b]", "/szse?codes=a,b", "/cffex?date=YYYYMMDD&prod=IF", "/index?code=sh000001&n=30"] });
    } catch (e) {
      return json({ error: String(e) }, 500);
    }
  }
};
