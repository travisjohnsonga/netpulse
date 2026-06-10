package agent

import (
	"reflect"
	"testing"
)

func TestMergeRoles(t *testing.T) {
	cases := []struct {
		name     string
		existing []string
		incoming []string
		want     []string
		added    bool
	}{
		{"adds new", []string{"web"}, []string{"web", "db"}, []string{"web", "db"}, true},
		{"no change when subset", []string{"web", "db"}, []string{"web"}, []string{"web", "db"}, false},
		{"from empty", nil, []string{"web"}, []string{"web"}, true},
		{"empty incoming", []string{"web"}, nil, []string{"web"}, false},
		{"skips blank", []string{"web"}, []string{""}, []string{"web"}, false},
		{"dedups incoming", nil, []string{"web", "web"}, []string{"web"}, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, added := mergeRoles(c.existing, c.incoming)
			if added != c.added {
				t.Errorf("added = %v, want %v", added, c.added)
			}
			if len(got) != len(c.want) || (len(got) > 0 && !reflect.DeepEqual(got, c.want)) {
				t.Errorf("merged = %v, want %v", got, c.want)
			}
		})
	}
}
