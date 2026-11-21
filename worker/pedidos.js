// Botmax · recibe pedidos de la página para invitados y los deja en GitHub como "pendiente".
// Secretos del Worker: GH_TOKEN (token de GitHub con permiso SOLO de Issues en el repo Botmax).
const REPO = 'finanzasens60-maker/Botmax';
const ORIGIN = 'https://finanzasens60-maker.github.io';

const cors = {
  'Access-Control-Allow-Origin': ORIGIN,
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { ...cors, 'Content-Type': 'application/json' } });
const limpio = (v, n) => String(v || '').replace(/[\r\n\[\]#<>`*_|]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);

export default {
  async fetch(req, env) {
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    const url = new URL(req.url);
    if (req.method !== 'POST' || url.pathname !== '/pedido') return json({ error: 'No encontrado' }, 404);
    if ((req.headers.get('Origin') || '') !== ORIGIN) return json({ error: 'Origen no permitido' }, 403);

    let d;
    try { d = await req.json(); } catch { return json({ error: 'Datos inválidos' }, 400); }
    if (d.web) return json({ ok: true, numero: 0 }); // trampa para bots
    const link = String(d.url || '').trim();
    if (!/^https?:\/\/\S{6,490}$/.test(link)) return json({ error: 'El link debe empezar por http' }, 400);
    const nombre = limpio(d.nombre, 40);
    if (nombre.length < 2) return json({ error: 'Escribe tu nombre' }, 400);
    const tema = limpio(d.tema, 40) || 'No response';
    const tipo = d.tipo === 'clips' ? 'clips' : 'resumen';

    let title, body;
    if (tipo === 'resumen') {
      const partes = Math.min(4, Math.max(1, parseInt(d.partes, 10) || 1));
      const minutos = [8, 10, 12, 15].includes(parseInt(d.minutos, 10)) ? parseInt(d.minutos, 10) : 10;
      title = `[resumen] [externo] ${nombre}`;
      body = `### Link del directo o de la grabación\n\n${link}\n\n### Cuántos videos\n\n${partes}\n\n` +
             `### Duración de cada video (minutos)\n\n${minutos}\n\n### Juego o tema\n\n${tema}\n\n---\nPedido de **${nombre}** desde la página para invitados.`;
    } else {
      const desde = /^\d{1,2}(:\d{2}){0,2}$/.test(String(d.desde || '').trim()) ? String(d.desde).trim() : '';
      const min = Math.min(120, Math.max(10, parseInt(d.duracion, 10) || 60));
      title = `[clips] [externo] ${nombre}`;
      body = `### Link del directo\n\n${link}\n\n### Desde qué minuto analizar\n\n${desde || 'No response'}\n\n` +
             `### Cuántos minutos analizar\n\n${min}\n\n### Juego o tema\n\n${tema}\n\n---\nPedido de **${nombre}** desde la página para invitados.`;
    }

    const r = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${String(env.GH_TOKEN || '').trim()}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'botmax-pedidos',
      },
      body: JSON.stringify({ title, body, labels: ['pendiente'] }),
    });
    if (!r.ok) return json({ error: 'No pude registrar el pedido, intenta en un rato', codigo: r.status }, 502);
    const issue = await r.json();
    return json({ ok: true, numero: issue.number });
  },
};
