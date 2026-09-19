using NBitcoin;
using System;
using System.Collections.Generic;
using System.Text;

namespace NBXplorer.Models
{
    public class GetFeeRateResult
    {
		public FeeRate FeeRate
		{
			get; set;
		}

		public int BlockCount
		{
			get; set;
		}
	}
}
