// 跨模块共享的可变状态。ES 模块的导入绑定是只读的，模块之间要共享可变数据
// 只能挂在同一个对象的属性上，而不是各自导出 let 变量——所以整个前端只有
// 这一份状态容器，其余模块一律读写 state.xxx。
export const state = {
  // DOM 骨架引用（boot() 中一次性取好）
  content: null, main: null, app: null, sidebar: null, toc: null,
  dragRegion: null, docTitle: null,
  searchBar: null, searchInput: null, searchCount: null,

  // 当前文档 / 跳转历史
  currentPath: null,
  navHistory: [], navIndex: -1, navGeneration: 0,

  // 目录：滚动高亮
  headings: [], activeLink: null,
  spyTick: false, spyLock: false, spyLockTimer: null,

  // 搜索
  searchHits: [], searchIdx: -1, searchTimer: null,

  // 图片选中 / 灯箱（图片与 Mermaid 图示共用）
  selectedImage: null,
  imageViewer: null, imageViewerReturnFocus: null,
  imageViewerScale: 1, imageViewerFitScale: 1, imageViewerUserAdjusted: false,
  imageViewerKind: "image", imageViewerNatural: { w: 0, h: 0 }, imageViewerSvg: null,
  imagePan: null,

  // Mermaid 按需加载
  mermaidLoadPromise: null, mermaidRenderSequence: 0,

  // toast
  toastTimer: null,

  // 编辑模式：Obsidian 风格 Live Preview
  editMode: false, editDirty: false, editBaseline: "", editMtimeNs: null, editPath: null,
  editPreviewSeq: 0, editEnterGen: 0, editSaveGen: 0, editSaving: false,
  editorText: "",
  editorLive: null, editorLiveWrap: null, editorPane: null, editorStatus: null,
  lpBlocks: [], activeBlockIdx: -1, lastHtmlParts: [], lpActivating: false,
  pendingEnterScroll: null, lpSingleRefreshSeq: 0, editRestoreScroll: null,
};
