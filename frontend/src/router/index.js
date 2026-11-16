export const routes=['实验档案','事件核对','流量分层','分流资格','实验裁决','发布记录'];export function page(){let n=Number(location.hash.slice(1))||0;return Math.min(Math.max(n,0),routes.length-1)}
