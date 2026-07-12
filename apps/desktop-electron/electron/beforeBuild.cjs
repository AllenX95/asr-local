'use strict';

module.exports = async function beforeBuild() {
  // Renderer dependencies are bundled by Vite; Python inference is packaged as an extra resource.
  return false;
};
