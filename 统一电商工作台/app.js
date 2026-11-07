const $ = (selector) => document.querySelector(selector);
async function request(path, options) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/portal/login') {
      showSession(null);
      productMessage(result.message || '会话失效，请重新登录商家后台');
    }
    throw new Error(result.message || '请求失败，请稍后重试');
  }
  return result;
}
let productPage = 1;
let selectedShop = '';
let moreProducts = false;
let sessionGeneration = 0;
let queryGeneration = 0;
let shopGeneration = 0;
let nextShop = null;
const productMessage = (message) => { $('#product-message').textContent = message; };
function showSession(session) {
  sessionGeneration++;
  queryGeneration++;
  shopGeneration++;
  $('#merchant-login').hidden = Boolean(session);
  $('#merchant-workspace').hidden = !session;
  $('#merchant-name').textContent = session ? `${session.tenant} · ${session.user}` : '';
  $('#refresh-shops').disabled = false;
  $('#more-shops').disabled = false;
  if (!session) {
    $('#product-results').replaceChildren();
    $('#product-pagination').hidden = true;
    $('#product-empty').textContent = '选择店铺后查看商品。';
    $('#product-search select').replaceChildren(new Option('请选择店铺', ''));
    $('#product-search select').disabled = true;
    $('#product-search button[type=submit]').disabled = true;
    $('#more-shops').hidden = true;
    selectedShop = '';
  }
}
async function loadShops(append = false) {
  const session = sessionGeneration;
  const generation = ++shopGeneration;
  const select = $('#product-search select');
  const cursor = append ? nextShop : '';
  if (!append) {
    queryGeneration++;
    select.replaceChildren(new Option('正在加载店铺…', ''));
    select.disabled = true;
    $('#product-search button[type=submit]').disabled = true;
    $('#product-results').replaceChildren();
    $('#product-pagination').hidden = true;
    $('#product-empty').textContent = '选择店铺后查看商品。';
  }
  $('#refresh-shops').disabled = true;
  $('#more-shops').disabled = true;
  try {
    const data = await request(`/portal/shops?${new URLSearchParams({ after: cursor || '' })}`);
    if (session !== sessionGeneration || generation !== shopGeneration) return;
    if (!Array.isArray(data.items) || (data.next_after !== null && typeof data.next_after !== 'string')) throw new Error('店铺列表响应无效');
    if (!append) select.replaceChildren(new Option('请选择店铺', ''));
    for (const shop of data.items) {
      if (typeof shop.id !== 'string' || typeof shop.name !== 'string' || typeof shop.platform !== 'string') throw new Error('店铺列表响应无效');
      select.add(new Option(`${shop.name} · ${shop.platform} (${shop.id})`, shop.id));
    }
    nextShop = data.next_after;
    $('#more-shops').hidden = !nextShop;
    select.disabled = select.options.length <= 1;
    $('#product-search button[type=submit]').disabled = select.disabled;
    productMessage(select.disabled ? '暂无可查看的演示店铺，请联系管理员授权。' : '请选择已授权的演示店铺。');
  } catch (error) {
    if (generation === shopGeneration) {
      if (!append) select.replaceChildren(new Option('店铺加载失败，请刷新', ''));
      productMessage(error.message);
    }
  } finally {
    if (generation === shopGeneration) { $('#refresh-shops').disabled = false; $('#more-shops').disabled = false; }
  }
}
async function request(path, options) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/portal/login') { showSession(null); productMessage(result.message || '会话失效，请重新登录'); }
    throw new Error(result.message || '请求失败，请稍后重试');
  }
  return result;
}
$('#merchant-login').addEventListener('submit', async (event) => {
  event.preventDefault();
  productMessage('统一登录在商家后台完成，请前往 http://127.0.0.1:8080/admin/login');
});
$('#merchant-logout').addEventListener('click', async () => {
  try { await request('/portal/logout', { method: 'POST' }); showSession(null); productMessage('已退出商家账号。'); }
  catch (error) { productMessage(error.message); }
});
$('#refresh-shops').addEventListener('click', () => void loadShops());
$('#more-shops').addEventListener('click', () => { if (nextShop) void loadShops(true); });
async function loadProducts() {
  const currentSession = sessionGeneration;
  const currentQuery = ++queryGeneration;
  productMessage('正在查询…');
  $('#product-results').replaceChildren();
  $('#product-pagination').hidden = true;
  $('#product-empty').textContent = '';
  try {
    const data = await request(`/portal/products?${new URLSearchParams({ shop_id: selectedShop, page: String(productPage) })}`);
    if (currentSession !== sessionGeneration || currentQuery !== queryGeneration) return;
    if (!Array.isArray(data.items)) throw new Error('商品服务响应无效');
    const list = $('#product-results');
    for (const item of data.items) {
      const row = document.createElement('li');
      const title = document.createElement('strong'); title.textContent = item.title;
      const status = { online: '在售', offline: '已下架', draft: '草稿' }[item.status] || item.status;
      const meta = document.createElement('span'); meta.textContent = `SKU ${item.sku} · 价格 ${item.price} · 库存 ${item.stock} · ${status}`;
      row.append(title, meta); list.append(row);
    }
    $('#product-empty').textContent = data.items.length ? '' : '这个店铺暂无商品。';
    moreProducts = Boolean(data.has_more);
    $('#product-pagination').hidden = productPage === 1 && !moreProducts;
    $('#previous-page').disabled = productPage === 1;
    $('#next-page').disabled = !moreProducts;
    $('#page-label').textContent = `第 ${productPage} 页`;
    productMessage('显示已授权演示店铺的商品。');
  } catch (error) { if (currentQuery === queryGeneration) productMessage(error.message); }
}
$('#product-search').addEventListener('submit', (event) => {
  event.preventDefault();
  selectedShop = event.currentTarget.elements.shop_id.value;
  productPage = 1;
  if (selectedShop) void loadProducts();
});
$('#previous-page').addEventListener('click', () => { if (productPage > 1) { productPage--; void loadProducts(); } });
$('#next-page').addEventListener('click', () => { if (moreProducts) { productPage++; void loadProducts(); } });
const initialGeneration = sessionGeneration;
fetch('/portal/session', { cache: 'no-store' })
  .then((response) => response.ok ? response.json() : null)
  .then((session) => { if (sessionGeneration === initialGeneration) { showSession(session); if (session) void loadShops(); } })
  .catch(() => { if (sessionGeneration === initialGeneration) showSession(null); });
