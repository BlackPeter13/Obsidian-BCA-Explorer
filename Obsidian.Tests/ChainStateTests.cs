using System.Collections.Generic;
using NBitcoin;
using NBitcoin.Altcoins;
using NBXplorer;
using NBXplorer.Models;
using Xunit;

namespace Obsidian.Tests
{
	/// <summary>
	/// Shared builders for chain-state tests. Everything is synthetic and
	/// offline: keys, addresses, transactions and chains are constructed in
	/// memory, no node involved.
	/// </summary>
	public class ChainStateContext
	{
		public readonly Network Network =
			BitcoinAtom.Instance.GetNetwork(NetworkType.Mainnet);

		public BitcoinAddress NewAddress() =>
			new Key().PubKey.GetAddress(Network);

		public Transaction Coinbase(BitcoinAddress to, decimal amount)
		{
			var tx = Network.Consensus.ConsensusFactory.CreateTransaction();
			tx.Inputs.Add(new TxIn(new OutPoint(uint256.Zero, uint.MaxValue))
			{
				ScriptSig = new Script(Op.GetPushOp(new byte[] { 0x01, 0x02 }))
			});
			tx.Outputs.Add(new TxOut(Money.Coins(amount), to.ScriptPubKey));
			return tx;
		}

		public Transaction Spend(OutPoint prevout, BitcoinAddress to, decimal amount)
		{
			var tx = Network.Consensus.ConsensusFactory.CreateTransaction();
			tx.Inputs.Add(new TxIn(prevout));
			tx.Outputs.Add(new TxOut(Money.Coins(amount), to.ScriptPubKey));
			return tx;
		}

		public TrackedTransaction Track(TrackedSource source, Transaction tx, uint256 blockHash) =>
			new TrackedTransaction(
				new TrackedTransactionKey(tx.GetHash(), blockHash, false),
				source, tx, new Dictionary<Script, KeyPath>());
	}

	/// <summary>
	/// Bug 3 (HTTP 500 on coinbase-heavy addresses): coinbase inputs spend the
	/// null prevout, which must never count as a double-spend. A genuine
	/// double-spend must still be reported as a conflict.
	/// </summary>
	public class UTXOStateTests : ChainStateContext
	{
		[Fact]
		public void TwoCoinbases_DoNotConflict()
		{
			var state = new UTXOState();
			var addr = NewAddress();
			var source = new AddressTrackedSource(addr);
			var block = new uint256(11);
			var cb1 = Track(source, Coinbase(addr, 3.125m), block);
			var cb2 = Track(source, Coinbase(addr, 6.25m), block);
			Assert.Equal(ApplyTransactionResult.Passed, state.Apply(cb1));
			Assert.Equal(ApplyTransactionResult.Passed, state.Apply(cb2));
		}

		[Fact]
		public void GenuineDoubleSpend_StillConflicts()
		{
			var state = new UTXOState();
			var addr = NewAddress();
			var source = new AddressTrackedSource(addr);
			var block = new uint256(12);
			var fund = Track(source, Coinbase(addr, 10m), block);
			Assert.Equal(ApplyTransactionResult.Passed, state.Apply(fund));
			var outpoint = new OutPoint(fund.TransactionHash, 0);
			var spend1 = Track(source, Spend(outpoint, NewAddress(), 9m), block);
			var spend2 = Track(source, Spend(outpoint, NewAddress(), 8m), block);
			Assert.Equal(ApplyTransactionResult.Passed, state.Apply(spend1));
			Assert.Equal(ApplyTransactionResult.Conflict, state.Apply(spend2));
		}
	}

	/// <summary>
	/// Bug 2 (HTTP 500 from conflicting records): two records both on the
	/// active chain that spend the same input still throw — that case is
	/// genuinely impossible. Stale side-branch records type as Orphan
	/// (SlimChain purges reorged branches) and never reach the confirmed set.
	/// </summary>
	public class ForkResolutionTests : ChainStateContext
	{
		private SlimChain MainChain(out uint256 h1, out uint256 h2)
		{
			var genesis = new uint256(1);
			var chain = new SlimChain(genesis);
			h1 = new uint256(11);
			h2 = new uint256(12);
			Assert.True(chain.TrySetTip(h1, genesis));
			Assert.True(chain.TrySetTip(h2, h1));
			return chain;
		}

		[Fact]
		public void StaleBranchRecord_TypesOrphanAndIsExcluded()
		{
			var addr = NewAddress();
			var source = new AddressTrackedSource(addr);
			var chain = MainChain(out var h1, out var h2);
			var staleBlock = new uint256(99); // never linked into the chain
			var fund = Track(source, Coinbase(addr, 10m), h1);
			var spent = new OutPoint(fund.TransactionHash, 0);
			// Stale record first: must not poison the main one that follows.
			var stale = Track(source, Spend(spent, NewAddress(), 9m), staleBlock);
			var main = Track(source, Spend(spent, NewAddress(), 8m), h2);
			Assert.Equal(AnnotatedTransactionType.Orphan, new AnnotatedTransaction(stale, chain).Type);
			var collection = new AnnotatedTransactionCollection(
				new[] {
					new AnnotatedTransaction(fund, chain),
					new AnnotatedTransaction(stale, chain),
					new AnnotatedTransaction(main, chain)
				}, source);
			Assert.Contains(collection.ConfirmedTransactions, t => t.Record.TransactionHash == main.TransactionHash);
			Assert.DoesNotContain(collection.ConfirmedTransactions, t => t.Record.TransactionHash == stale.TransactionHash);
		}

		[Fact]
		public void StaleBranchRecord_TypesOrphanAndIsExcluded_MainFirst()
		{
			var addr = NewAddress();
			var source = new AddressTrackedSource(addr);
			var chain = MainChain(out var h1, out var h2);
			var staleBlock = new uint256(99);
			var fund = Track(source, Coinbase(addr, 10m), h1);
			var spent = new OutPoint(fund.TransactionHash, 0);
			var main = Track(source, Spend(spent, NewAddress(), 8m), h2);
			var stale = Track(source, Spend(spent, NewAddress(), 9m), staleBlock);
			Assert.Equal(AnnotatedTransactionType.Orphan, new AnnotatedTransaction(stale, chain).Type);
			var collection = new AnnotatedTransactionCollection(
				new[] {
					new AnnotatedTransaction(fund, chain),
					new AnnotatedTransaction(main, chain),
					new AnnotatedTransaction(stale, chain)
				}, source);
			Assert.Contains(collection.ConfirmedTransactions, t => t.Record.TransactionHash == main.TransactionHash);
			Assert.DoesNotContain(collection.ConfirmedTransactions, t => t.Record.TransactionHash == stale.TransactionHash);
		}

		[Fact]
		public void SlimChain_PurgesReorgedHeaders()
		{
			// SlimChain keeps only the active chain: after a reorg the old
			// branch is no longer findable, so stale-branch match records
			// type as Orphan and never reach the confirmed set.
			var genesis = new uint256(1);
			var chain = new SlimChain(genesis);
			var h1 = new uint256(11);
			var h2 = new uint256(12);
			var s = new uint256(99);
			Assert.True(chain.TrySetTip(h1, genesis));
			Assert.True(chain.TrySetTip(h2, h1));
			Assert.True(chain.TrySetTip(s, h1));
			Assert.True(chain.TrySetTip(h2, h1));
			Assert.Equal(h2, chain.Tip);
			Assert.Null(chain.GetBlock(s));
			Assert.Equal(h2, chain.GetBlock(2).Hash);
		}

		[Fact]
		public void DoubleSpendOnMainChain_StillThrows()
		{
			var addr = NewAddress();
			var source = new AddressTrackedSource(addr);
			var chain = MainChain(out var h1, out var h2);
			var fund = Track(source, Coinbase(addr, 10m), h1);
			var spent = new OutPoint(fund.TransactionHash, 0);
			var a = Track(source, Spend(spent, NewAddress(), 9m), h2);
			var b = Track(source, Spend(spent, NewAddress(), 8m), h2);
			Assert.Throws<System.InvalidOperationException>(() => new AnnotatedTransactionCollection(
				new[] {
					new AnnotatedTransaction(fund, chain),
					new AnnotatedTransaction(a, chain),
					new AnnotatedTransaction(b, chain)
				}, source));
		}
	}
}
