import {defineConfig} from 'vite';
export default defineConfig({build:{rollupOptions:{output:{inlineDynamicImports:true}},assetsInlineLimit:0},server:{proxy:{'/api':'http://127.0.0.1:8765'}}});
