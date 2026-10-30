<script lang="ts">
  let { moves }: { moves: string[] } = $props();

  let turns = $derived.by(() => {
    const turns = [];

    for (let index = 0; index < moves.length; index += 2) {
      turns.push({
        number : index / 2 + 1,
        white  : moves[index],
        black  : moves[index + 1] ?? '—',
      });
    }

    return turns;
  });
</script>

<article>
  <h2 class="row">
    Moves <small>{moves.length}</small>
  </h2>

  <div class="moves" role="region" aria-label="Move history">
    {#if turns.length === 0}
      <p>Waiting for the first move.</p>
    {:else}
      <table>
        <thead>
          <tr>
            <th scope="col">Move</th>
            <th scope="col">White</th>
            <th scope="col">Black</th>
          </tr>
        </thead>
        <tbody>
          {#each turns as turn}
            <tr>
              <th scope="row">{turn.number}</th>
              <td>{turn.white}</td>
              <td>{turn.black}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</article>

<style>
  .moves {
    max-height : 250px;
    overflow   : auto;
    font-size  : .9rem;
  }
</style>
