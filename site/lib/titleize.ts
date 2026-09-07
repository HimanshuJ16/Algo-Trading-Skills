/** Slug to title, keeping initialisms the library uses upper-case. Presentation only. */
const UPPER = new Set([
  "api","us","eu","uk","ml","fx","sec","cftc","finra","mifid","fca","asic","sebi","mas",
  "iiroc","sfc","fsa","esma","hsm","mpc","dex","cme","cboe","lse","hkex","sgx","asx","jpx",
  "krx","twse","idx","moex","jse","nzx","dfm","lme","b3","ice","nyse","nasdaq","otc","var",
  "twap","vwap","pov","sor","itch","fix","ptp","numa","fpga","cpu","l2","l3","pnl","gst",
  "vat","kyc","aml","esg","nlp","iv","tca","dst","ci","db","sla","mt5","ibkr","tws","okx",
  "ftx","cds","trs","efp","span","isin","cusip","sedol","xetra","optiq","rl","ab","hf",
  "eod","pdt","mar","otr","dvc","1099","475","1256","15c3","nms","sho","rts","drt","oauth1",
  "pkce","grpc","vix","ab","1099-b",
]);

export function titleize(slug: string): string {
  return slug
    .split("-")
    .map((word) =>
      UPPER.has(word) ? word.toUpperCase() : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}
