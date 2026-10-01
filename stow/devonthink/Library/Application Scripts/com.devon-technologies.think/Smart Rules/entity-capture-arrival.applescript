on performSmartRule(theRecords)
	try
		do shell script "$HOME/.local/bin/should-run-dt-driver"
	on error
		return
	end try
	do shell script "/usr/bin/python3 \"$HOME/.local/bin/entity-capture-arrival\"" without altering line endings
end performSmartRule
