// Rehearsal-only TCP relay: one fixed private Odoo destination, host loopback only.
// Internal Docker networks keep external egress disabled; no request-selected target.
const net = require('node:net');
const host = process.argv[2];
const octets = typeof host === 'string' ? host.split('.').map(Number) : [];
if (net.isIP(host || '') !== 4 || !(octets[0] === 10 || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) || (octets[0] === 192 && octets[1] === 168))) {
  throw new Error('Expected the private IPv4 address of the rehearsal Odoo container.');
}
const sockets = new Set();
const server = net.createServer(client => {
  const upstream = net.connect({host, port: 8069});
  sockets.add(client); sockets.add(upstream);
  const close = () => {client.destroy(); upstream.destroy(); sockets.delete(client); sockets.delete(upstream);};
  client.on('error', close); upstream.on('error', close);
  client.on('close', close); upstream.on('close', close);
  client.setTimeout(60000, close); upstream.setTimeout(60000, close);
  client.pipe(upstream); upstream.pipe(client);
});
server.on('error', error => {console.error(error.code); process.exitCode = 1;});
server.listen(8069, '127.0.0.1', () => console.log('Rehearsal Odoo relay ready on host loopback.'));
process.on('SIGTERM', () => {for (const socket of sockets) socket.destroy(); server.close();});
