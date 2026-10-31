// ETF classification reuses vendor/src/country_etf_tactical/universe.py ALL_TICKERS
// (excluding the separate GC=F and BTC-USD assets). Stock picks come from each
// month's actual rebalance metadata; unknown assets are never silently stocks.
const etfs = new Set(['ARGT','BIL','DXJ','ECH','EDEN','EGPT','EIDO','EIRL','EIS','ENZL','EPI','EPOL','EPU','EWA','EWC','EWD','EWG','EWI','EWJ','EWL','EWM','EWN','EWP','EWQ','EWS','EWT','EWU','EWW','EWY','EWZ','EZA','GREK','GXG','IEF','INDA','IWF','KSA','KWEB','MCHI','MTUM','NORW','PAK','QAT','QQQ','SCHG','SPMO','SPY','SPYG','THD','TUR','UAE','VGT','VNM','VUG','XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY']);
export const segmentDefinitions = [
  {id:'stock',label:'Stock',color:'#73e6c2'},
  {id:'etf',label:'ETF',color:'#789eff'},
  {id:'gold',label:'Gold',color:'#e9bb78'},
  {id:'btc',label:'BTC',color:'#c3a4f6'},
  {id:'cash',label:'Cash',color:'#657689'},
];

export function groupAllocation(weights, stockPicks='') {
  const stocks = new Set(stockPicks.split(',').filter(Boolean));
  const groups = segmentDefinitions.map(d=>({...d,total:0,items:[]}));
  for(const [ticker,weight] of Object.entries(weights)) {
    const id = ticker==='CASH'?'cash':ticker==='GC=F'?'gold':ticker==='BTC-USD'?'btc':
      stocks.has(ticker)?'stock':etfs.has(ticker)?'etf':'other';
    let group = groups.find(g=>g.id===id);
    if(!group){group={id:'other',label:'Unclassified',color:'#d88ca9',total:0,items:[]};groups.push(group);}
    group.total+=weight;group.items.push({ticker,weight});
  }
  groups.forEach(g=>g.items.sort((a,b)=>b.weight-a.weight));
  return groups;
}
