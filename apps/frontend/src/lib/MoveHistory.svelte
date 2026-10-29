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

<section class="panel">
  <h2>Moves <span class="muted">{moves.length}</span></h2>
  <div class="moves" role="region" aria-label="Move history">
    {#if turns.length === 0}
      <p class="muted">Waiting for the first move.</p>
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
</section>

<style>
  .moves {
    max-height : 250px;
    overflow-y : auto;
    font-size  : .9rem;
  }

  table {
    width                : 100%;
    border-collapse      : collapse;
    text-align           : left;
    font-variant-numeric : tabular-nums;
  }

  th, td { padding: 5px 8px; }

  thead { color: #647264; }

  tbody tr:nth-child(odd) { background: #f2f4ed; }

  tbody th { font-weight: 400; color: #647264; }
</style>
