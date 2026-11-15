/* Shared Game Boy catalog slug + detail-URL helpers for Homebrew Hive.
   Must stay in sync with /workspace/hive-cleanurl/migration.sql slugify(). */
(function (global) {
  var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function isUuid(value) {
    return UUID_RE.test(String(value || ''));
  }

  function slugify(title) {
    return String(title || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
  }

  /* Prefer a stored slug. If the column is missing (current live DB), use a
     non-UUID id as the slug, otherwise slugify(title). */
  function canonicalSlug(game) {
    if (!game) return '';
    if (game.slug) return String(game.slug);
    if (game.id && !isUuid(game.id)) return String(game.id);
    return slugify(game.title);
  }

  function gameDetailPath(game) {
    var slug = canonicalSlug(game);
    return slug ? '/game-detail/' + encodeURIComponent(slug) : '/games.html';
  }

  function pathSlug(pathname) {
    var path = String(pathname || '');
    var match = path.match(/^\/game-detail\/([^/]+)\/?$/);
    if (!match) return '';
    try {
      return decodeURIComponent(match[1]);
    } catch (e) {
      return match[1];
    }
  }

  function isGameDetailPath(pathname) {
    var path = String(pathname || '');
    return path === '/game-detail' || path === '/game-detail/' || path === '/game-detail.html' || /^\/game-detail\/[^/]+\/?$/.test(path);
  }

  function gameMatchesKey(game, key) {
    if (!game || !key) return false;
    if (game.id === key) return true;
    if (game.slug && game.slug === key) return true;
    return slugify(game.title) === key;
  }

  function findGame(games, key) {
    if (!games || !key) return null;
    var i, game;
    for (i = 0; i < games.length; i++) {
      game = games[i];
      if (game && game.id === key) return game;
    }
    for (i = 0; i < games.length; i++) {
      game = games[i];
      if (game && game.slug && game.slug === key) return game;
    }
    for (i = 0; i < games.length; i++) {
      game = games[i];
      if (game && slugify(game.title) === key) return game;
    }
    return null;
  }

  global.HiveSlugs = {
    isUuid: isUuid,
    slugify: slugify,
    canonicalSlug: canonicalSlug,
    gameDetailPath: gameDetailPath,
    pathSlug: pathSlug,
    isGameDetailPath: isGameDetailPath,
    gameMatchesKey: gameMatchesKey,
    findGame: findGame
  };
})(window);
