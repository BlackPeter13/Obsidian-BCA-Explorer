using NBitcoin;
using System;

namespace NBitcoin.Altcoins
{
	/// <summary>
	/// BCA wire consensus for peers >= BCA_HARD_FORK_VERSION (70020):
	/// every header carries a trailing nFlags word (84 bytes). PoS-flagged
	/// blocks (bit 0) commit the masked flags into the block id, everything
	/// else hashes the classic 80 bytes. Rule proven against BCA Core
	/// (src/primitives/block.{h,cpp}, CBlockHeader::GetHash) and node truth:
	/// mainnet 1025208 (flags 0x80000000) hashes 80 bytes,
	///          1025209 (flags 0x80000001) hashes 80 + 01000000.
	/// The masked read keeps even the non-virtual base GetHash correct,
	/// because it hashes whatever ReadWrite serializes.
	/// </summary>
	public class BCAConsensusFactory : ConsensusFactory
	{
		public override BlockHeader CreateBlockHeader()
		{
			return new BCABlockHeader();
		}
	}

	public class BCABlockHeader : BlockHeader
	{
		public uint NFlags;

		public override void ReadWrite(BitcoinStream stream)
		{
			base.ReadWrite(stream);
			if (stream.Serializing)
			{
				// Hashing only (BCA headers are never re-broadcast): emit the
				// exact preimage the chain commits to, omit the word otherwise.
				if (NFlags != 0)
				{
					uint flags = NFlags;
					stream.ReadWrite(ref flags);
				}
			}
			else
			{
				stream.ReadWrite(ref NFlags);
				NFlags &= 1u;
			}
		}
	}
}
