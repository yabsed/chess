<script lang="ts">
  import { http } from './http';
  import type { User } from './game/protocol';

  let { user, onuser }: { user: User | null; onuser: (user: User | null) => void } = $props();

  let registering = $state(false);
  let username    = $state('');
  let password    = $state('');
  let busy        = $state(false);
  let error       = $state('');
  let picker = $state<HTMLInputElement>();

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    busy  = true;
    error = '';
    try {
      const path = registering ? 'register' : 'session';
      onuser(await http.post(path, { json: { username, password } }).json<User>());
      password = '';
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Unable to sign in.';
    } finally {
      busy = false;
    }
  }

  async function logout() {
    busy  = true;
    error = '';
    try {
      await http.delete('session');
      onuser(null);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Unable to sign out.';
    } finally {
      busy = false;
    }
  }

  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file  = input.files?.[0];
    if (!file) return;

    error = '';
    if (file.size > 2 * 1024 * 1024) {
      error = 'Choose an image smaller than 2 MiB.';
      input.value = '';
      return;
    }

    busy = true;
    try {
      onuser(await http.put('me/avatar', { body: file }).json<User>());
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Unable to upload this image.';
    } finally {
      input.value = '';
      busy = false;
    }
  }
</script>

{#if user}
  <div class="row account">
    <div class="identity">
      <button class="avatar" disabled={busy} onclick={() => picker?.click()} aria-label="Change profile picture">
        {#if user.avatar}
          <img src={user.avatar} alt="" />
        {:else}
          {user.username[0].toUpperCase()}
        {/if}
      </button>
      <input bind:this={picker} type="file" accept="image/jpeg,image/png,image/webp" hidden onchange={upload} />
      <div>
        <strong>{user.username}</strong>
        <small>Click your picture to change it</small>
      </div>
    </div>
    <button class="secondary" disabled={busy} onclick={logout}>Sign out</button>
  </div>
{:else}
  <article class="login">
    <h1>{registering ? 'Join Small Chess' : 'Welcome to Small Chess'}</h1>
    <p>Sign in, find an opponent, make your move.</p>
    <form onsubmit={submit}>
      <label for="username">Username</label>
      <input id="username" bind:value={username} autocomplete="username" required
        pattern={'[a-zA-Z0-9_]{3,24}'} minlength="3" maxlength="24"
        aria-describedby="username-hint" />
      <small id="username-hint">3–24 letters, numbers or underscores. Case-insensitive.</small>

      <label for="password">Password</label>
      <input id="password" bind:value={password} type="password" required minlength="8" maxlength="128"
        autocomplete={registering ? 'new-password' : 'current-password'} />
      <small>At least 8 characters.</small>

      <div class="actions">
        <button disabled={busy}>
          {#if busy}
            Please wait…
          {:else if registering}
            Create account
          {:else}
            Sign in
          {/if}
        </button>
        <button type="button" class="secondary" disabled={busy} onclick={() => {
          registering = !registering;
          error = '';
        }}>
          {registering ? 'Already have an account?' : 'Create an account'}
        </button>
      </div>
    </form>
  </article>
{/if}

{#if error}
  <p class="notice error" role="alert">{error}</p>
{/if}

<style>
  .account { margin: 24px 0; }
  .account button { margin: 0; }
  .account small { display: block; }
  .login { max-width: 460px; margin: 40px auto; }
  form > small { display: block; margin-bottom: 16px; }
  form .actions { margin-top: 24px; }
</style>
