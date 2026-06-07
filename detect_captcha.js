/* 撞码检测探针 —— 每次 navigate/scrape 后用 javascript_tool 跑一次。
 * 只检测+分类+读题(用于路由/通知),绝不作答/点选/绕过。
 * 返回 {blocked, platform, type, prompt, cards, url}。
 * 三态:blocked=true → 撞码(暂停+通知+喊人);blocked=false 且 cards>0 → 正常;
 *       blocked=false 且 cards===0 → 交给"空态/登录"二次判别(真没结果 or 软失败重试)。
 */
(function(){
  var host=location.host, t=(document.title||""), txt=((document.body&&document.body.innerText)||"").slice(0,3000);
  var o={blocked:false, platform:"", type:"", prompt:"", cards:0, url:location.href};
  if(host.indexOf("douyin")>=0){
    o.platform="抖音";
    o.cards=document.querySelectorAll('.search-result-card,a[href*="/video/"]').length;
    if(o.cards===0 && /验证码|点击.*形状|拖动.*滑块|滑块|安全验证|verify/i.test(t+" "+txt)) { o.blocked=true; o.type="抖音验证(点选/滑块)"; }
  } else if(host.indexOf("xiaohongshu")>=0){
    o.platform="小红书";
    o.cards=document.querySelectorAll('a[href*="/explore/"]').length;
    var cap = /安全验证|请选择.*(图片|物体)|符合描述/.test(txt) || !!document.querySelector('[class*=captcha],[class*=verify],[class*=vc-]');
    if(o.cards===0 && cap){ o.blocked=true; o.type="小红书选图验证"; }
  }
  if(o.blocked){ o.prompt=((txt.match(/(请选择[^。\n]{0,30}|点击[^。\n]{0,30}|安全验证|拖动[^。\n]{0,20})/)||[])[0])||t; }
  return o;
})()
