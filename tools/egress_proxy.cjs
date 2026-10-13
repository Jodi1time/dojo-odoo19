const http=require('node:http'),net=require('node:net');
const allowed=new Set(['api.twilio.com:443']);
const server=http.createServer((request,response)=>{response.writeHead(403);response.end();});
server.on('connect',(request,socket,head)=>{
  const target=String(request.url||'');
  console.log(`${new Date().toISOString()} CONNECT ${target} ${allowed.has(target)?'allowed':'denied'}`);
  if(!allowed.has(target)){socket.end('HTTP/1.1 403 Forbidden\r\n\r\n');return;}
  const upstream=net.connect(443,'api.twilio.com',()=>{socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)upstream.write(head);upstream.pipe(socket);socket.pipe(upstream);});
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());
});
server.listen(3128,'0.0.0.0');
