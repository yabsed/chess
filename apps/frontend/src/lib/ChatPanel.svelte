<script lang="ts">
  import type { Chat } from './types';

  interface Props {
    messages : Chat[];
    you      : string;

    connected      : boolean;
    loadingHistory : boolean;
    hasEarlier     : boolean;

    onsend    : (content: string) => boolean;
    onhistory : () => void;
  }

  let {
    messages, you,
    connected, loadingHistory, hasEarlier,
    onsend, onhistory,
  }: Props = $props();

  let draft = $state('');

  let content = $derived(draft.trim());
  let length  = $derived([...content].length);
  let canSend = $derived(connected && length > 0 && length <= 140);

  let messageList  = $state<HTMLDivElement>();
  let followLatest = true;

  function send(event: SubmitEvent) {
    event.preventDefault();
    if (!canSend) return;

    if (onsend(content)) {
      draft        = '';
      followLatest = true;
    }
  }

  function loadEarlier() {
    followLatest = false;
    if (messageList) messageList.scrollTop = 0;
    onhistory();
  }

  function trackScroll() {
    if (!messageList) return;

    const remaining = messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
    followLatest = remaining < 24;
  }

  $effect(() => {
    messages.length;
    if (messageList && followLatest) {
      messageList.scrollTop = messageList.scrollHeight;
    }
  });
</script>

<section class="panel">
  <h2>Chat</h2>

  {#if hasEarlier}
    <button class="text-button history"
            disabled={!connected || loadingHistory}
            onclick={loadEarlier}>
      {loadingHistory ? 'Loading…' : 'Earlier messages'}
    </button>
  {/if}

  <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to scroll the chat history.) -->
  <div class="messages" bind:this={messageList}
       role="log" aria-label="Chat messages" aria-relevant="additions"
       tabindex="0" onscroll={trackScroll}>
    {#each messages as message (message.id)}
      <p class="chat-message">
        <strong>{message.sender}{message.sender === you ? ' · You' : ''}</strong>
        <span>{message.content}</span>
      </p>
    {:else}
      <p class="muted">No messages yet.</p>
    {/each}
  </div>

  <form onsubmit={send}>
    <label for="chat-message">Message</label>
    <div class="compose">
      <input id="chat-message" type="text" bind:value={draft}
             disabled={!connected} autocomplete="off"
             aria-describedby="chat-limit" aria-invalid={length > 140}
             onkeydown={(event) => {
               if (event.key === 'Enter' && event.isComposing) event.preventDefault();
             }} />
      <button type="submit" disabled={!canSend}>Send</button>
    </div>
    <small id="chat-limit" class:error={length > 140}>
      {connected ? `${length}/140` : 'Connect to send messages.'}
    </small>
  </form>
</section>

<style>
  .history { margin: -6px 0 10px; }

  .messages {
    min-height : 80px;
    max-height : 220px;
    overflow-y : auto;
    font-size  : .85rem;
  }

  .messages:focus-visible { outline: 2px solid #5671d6; outline-offset: 3px; }

  .chat-message { margin: 0 0 12px; overflow-wrap: anywhere; }
  .chat-message:last-child { margin-bottom: 0; }

  .chat-message strong {
    display        : block;
    font-size      : .75rem;
    color          : #647264;
    text-transform : capitalize;
  }

  .chat-message span { white-space: pre-wrap; }

  form {
    margin-top  : 16px;
    padding-top : 12px;
    border-top  : 1px solid #dce1d6;
  }

  label { display: block; margin-bottom: 6px; font-size: .8rem; }

  .compose {
    display               : grid;
    grid-template-columns : minmax(0, 1fr) auto;
    gap                   : 6px;
  }

  input {
    width         : 100%;
    padding       : 8px;
    border        : 1px solid #ccd4c8;
    border-radius : 4px;

    background : #f6f5ef;
    color      : #233c2d;
    font-size  : .85rem;
  }

  button[type='submit'] { padding: 8px 12px; font-size: .85rem; }
  small { display: block; margin-top: 4px; text-align: right; }
</style>
